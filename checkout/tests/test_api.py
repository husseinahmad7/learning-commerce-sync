from unittest.mock import patch

from django.db import transaction
from django.test import TestCase
from rest_framework.test import APIClient

from checkout.models import Course, Enrollment, EnrollmentOutbox, ERPSyncLog, Student

from .helpers import PAYMENT, make_course


class CheckoutTests(TestCase):
    def setUp(self):
        make_course()
        self.client = APIClient()

    def test_checkout_returns_200_without_contacting_erp_or_broker(self):
        with patch("checkout.tasks.post_to_erp", side_effect=AssertionError("ERP must be async")) as erp:
            with patch("checkout.tasks.sync_enrollment.apply_async", side_effect=AssertionError("No broker in checkout")) as publish:
                response = self.client.post("/api/checkout/", PAYMENT, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["created"])
        self.assertEqual(response.data["sync_status"], "Pending")
        self.assertEqual(Enrollment.objects.count(), 1)
        log = ERPSyncLog.objects.get()
        self.assertEqual(log.payload["invoice"]["amount_paid"], "99.00")
        self.assertEqual(log.payload["student"]["name"], "Ada Demo")
        erp.assert_not_called()
        publish.assert_not_called()

    def test_identical_replay_returns_the_same_enrollment(self):
        first = self.client.post("/api/checkout/", PAYMENT, format="json")
        replay = self.client.post("/api/checkout/", PAYMENT, format="json")
        self.assertEqual(replay.status_code, 200)
        self.assertFalse(replay.data["created"])
        self.assertEqual(first.data["enrollment_id"], replay.data["enrollment_id"])
        self.assertEqual(Enrollment.objects.count(), 1)
        self.assertEqual(ERPSyncLog.objects.count(), 1)

    def test_reused_transaction_with_changed_payment_returns_409(self):
        self.client.post("/api/checkout/", PAYMENT, format="json")
        response = self.client.post("/api/checkout/", {**PAYMENT, "amount_paid": "1.00"}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Enrollment.objects.count(), 1)
        self.assertEqual(ERPSyncLog.objects.get().payload["invoice"]["amount_paid"], "99.00")

    def test_transaction_id_rejects_unicode_whitespace_and_header_controls(self):
        for value in ("txn-\u2603", "txn\nHeader", "txn\r\nHeader", "txn\t1", " txn-1", "txn-1 ", "", "a" * 101):
            with self.subTest(transaction_id=value):
                response = self.client.post("/api/checkout/", {**PAYMENT, "transaction_id": value}, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn("transaction_id", response.data)
        self.assertEqual(Enrollment.objects.count(), 0)
        self.assertEqual(ERPSyncLog.objects.count(), 0)
        self.assertEqual(Student.objects.count(), 0)

    def test_transaction_id_preserves_safe_punctuation(self):
        value = "PAYMENT_001.v2:retry-1"
        response = self.client.post("/api/checkout/", {**PAYMENT, "transaction_id": value}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["transaction_id"], value)
        self.assertEqual(ERPSyncLog.objects.get().payload["invoice"]["transaction_id"], value)

    def test_invalid_amount_and_unknown_course_do_not_create_enrollments(self):
        for changes in ({"amount_paid": "-1.00"}, {"course_code": "MISSING"}, {"student_email": "invalid"}):
            with self.subTest(changes=changes):
                response = self.client.post("/api/checkout/", {**PAYMENT, **changes}, format="json")
                self.assertEqual(response.status_code, 400)
        self.assertEqual(Enrollment.objects.count(), 0)
        self.assertEqual(ERPSyncLog.objects.count(), 0)

    def test_enrollment_rolls_back_if_durable_log_cannot_be_saved(self):
        with patch("checkout.services.ERPSyncLog.objects.create", side_effect=RuntimeError("write failed")):
            with self.assertRaises(RuntimeError):
                self.client.post("/api/checkout/", PAYMENT, format="json")
        self.assertEqual(Enrollment.objects.count(), 0)
        self.assertEqual(Student.objects.count(), 0)
        self.assertEqual(EnrollmentOutbox.objects.count(), 0)

    def test_snapshot_survives_profile_and_course_edits(self):
        response = self.client.post("/api/checkout/", PAYMENT, format="json")
        Student.objects.update(name="Edited profile")
        Course.objects.update(name="Edited course", price="1.00")
        detail = self.client.get(f'/api/enrollments/{response.data["enrollment_id"]}/')
        self.assertEqual(detail.data["payload"]["student"]["name"], "Ada Demo")
        self.assertEqual(detail.data["payload"]["invoice"]["course_name"], "Python Basics")
        self.assertEqual(detail.data["payload"]["invoice"]["amount_paid"], "99.00")

    def test_rolled_back_checkout_never_becomes_dispatchable(self):
        with transaction.atomic():
            self.client.post("/api/checkout/", PAYMENT, format="json")
            transaction.set_rollback(True)
        self.assertEqual(ERPSyncLog.objects.count(), 0)
