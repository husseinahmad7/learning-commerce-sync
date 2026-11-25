import json
from datetime import timedelta
from io import BytesIO
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import LiveServerTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from checkout.erp import ERPResponse, UnknownInvoiceOutcome, lookup_mock_invoice, post_to_erp
from checkout.models import EnrollmentOutbox, ERPSyncLog, LearningEnrollment
from checkout.sync import sync_one
from checkout.tasks import mark_invoice_deadlines, sync_learning
from mock_erp.models import MockERPInvoice

from .helpers import PAYMENT, make_course, make_pending


class WorkflowTests(TestCase):
    def setUp(self):
        make_course()
        self.client = APIClient()

    def test_payment_atomically_queues_both_consumers_and_enrollment_is_idempotent(self):
        checkout = self.client.post("/api/checkout/", PAYMENT, format="json")
        self.assertEqual(checkout.status_code, 200)
        self.client.post("/api/checkout/", PAYMENT, format="json")
        self.assertEqual(ERPSyncLog.objects.count(), 1)
        delivery = EnrollmentOutbox.objects.get()
        self.assertEqual(sync_learning.run(delivery.pk), "enrolled")
        self.assertEqual(sync_learning.run(delivery.pk), "skipped")
        second_log = make_pending(transaction_id="payment-2")
        self.assertEqual(sync_learning.run(second_log.enrollment.delivery.pk), "enrolled")
        self.assertEqual(LearningEnrollment.objects.count(), 1)
        detail = self.client.get(f'/api/enrollments/{checkout.data["enrollment_id"]}/').data
        self.assertEqual(detail["invoice_outcome"], "Pending")
        self.assertEqual(detail["learning_status"], "Enrolled")

    def test_lost_response_reconciles_real_mock_invoice_without_reposting(self):
        log = make_pending()
        key = log.payload["invoice"]["transaction_id"]

        def commit_then_timeout(payload):
            result = self.client.post("/mock-erp/sync/", payload, format="json", HTTP_IDEMPOTENCY_KEY=key)
            self.assertEqual(result.status_code, 200)
            raise UnknownInvoiceOutcome("reply lost")

        def lookup(payload):
            result = self.client.get("/mock-erp/invoice-status/", {"transaction_id": key})
            return ERPResponse(result.status_code, result.data.get("invoice_id", ""))

        self.assertIsInstance(sync_one(log.pk, post=commit_then_timeout, circuit=None).error, UnknownInvoiceOutcome)
        log.refresh_from_db()
        self.assertEqual(log.invoice_outcome, "Unknown")
        self.assertEqual(list(log.attempts.order_by("id").values_list("outcome", flat=True)), ["Started", "Unknown"])
        ERPSyncLog.objects.filter(pk=log.pk).update(next_attempt_at=timezone.now())
        never_post = Mock(side_effect=AssertionError("Cannot send an unresolved invoice"))
        self.assertEqual(sync_one(log.pk, post=never_post, lookup=lookup, circuit=None).status, "Success")
        never_post.assert_not_called()
        log.refresh_from_db()
        self.assertEqual(log.invoice_outcome, "Accepted")
        self.assertTrue(log.erp_invoice_id.startswith("MOCK-INV-"))

    def test_crash_before_confirmation_leaves_started_record_and_reconciles(self):
        log = make_pending()
        with self.assertRaises(SystemExit):
            sync_one(log.pk, post=Mock(side_effect=SystemExit), circuit=None)
        log.refresh_from_db()
        self.assertEqual(log.invoice_outcome, "Unknown")
        self.assertEqual(log.attempts.get().outcome, "Started")
        ERPSyncLog.objects.filter(pk=log.pk).update(next_attempt_at=timezone.now())
        never_post = Mock(side_effect=AssertionError("Cannot send an unresolved invoice"))
        # Simulator-only 404 permits another send, but never during lookup.
        self.assertEqual(sync_one(log.pk, post=never_post, lookup=lambda _: ERPResponse(404), circuit=None).status, "Pending")
        never_post.assert_not_called()
        log.refresh_from_db()
        self.assertEqual(log.invoice_outcome, "Pending")

    def test_status_404_must_carry_the_simulator_contract(self):
        log = make_pending()
        lookup_error = HTTPError("http://example.test/", 404, "not found", {}, BytesIO(b"missing page"))
        with patch("checkout.erp.urlopen", side_effect=lookup_error):
            with self.assertRaisesMessage(UnknownInvoiceOutcome, "did not confirm"):
                lookup_mock_invoice(log.payload)

    def test_rejection_requires_correction_and_marks_operator_attention(self):
        log = make_pending()
        post = Mock(return_value=ERPResponse(422))
        self.assertEqual(sync_one(log.pk, post=post, circuit=None).status, "Failed")
        self.assertEqual(sync_one(log.pk, post=post, circuit=None).status, "skipped")
        with self.assertRaisesMessage(CommandError, "corrected document"):
            call_command("retry_sync", log.enrollment_id)
        self.assertEqual(post.call_count, 1)
        log.refresh_from_db()
        self.assertEqual(log.invoice_outcome, "Rejected")
        self.assertIsNotNone(log.alert_at)

    def test_due_alert_is_durable_and_does_not_include_accepted_invoices(self):
        overdue = make_pending()
        accepted = make_pending(transaction_id="payment-2")
        ERPSyncLog.objects.update(created_at=timezone.now() - timedelta(hours=23, minutes=1))
        ERPSyncLog.objects.filter(pk=accepted.pk).update(invoice_outcome="Accepted")
        self.assertEqual(mark_invoice_deadlines(), 1)
        self.assertEqual(mark_invoice_deadlines(), 0)
        overdue.refresh_from_db()
        accepted.refresh_from_db()
        self.assertIsNotNone(overdue.alert_at)
        self.assertIsNone(accepted.alert_at)


class DeliveryHTTPTests(LiveServerTestCase):
    def test_confirmed_payment_and_lost_invoice_reply_converge_over_http(self):
        make_course()
        request = Request(f"{self.live_server_url}/api/checkout/",
                          data=json.dumps(PAYMENT).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            payment = json.load(response)
        log = ERPSyncLog.objects.get(enrollment_id=payment["enrollment_id"])
        self.assertEqual(sync_learning.run(log.enrollment.delivery.pk), "enrolled")
        with override_settings(MOCK_ERP_URL=f"{self.live_server_url}/mock-erp/sync/",
                               MOCK_ERP_RANDOM_FAILURES=True, SYNC_RETRY_BASE_SECONDS=0):
            self.assertEqual(lookup_mock_invoice(log.payload).status_code, 404)
            with patch("checkout.erp.random.random", return_value=0.45):
                self.assertIsInstance(sync_one(log.pk, post=post_to_erp, circuit=None).error, UnknownInvoiceOutcome)
            self.assertEqual(MockERPInvoice.objects.count(), 1)
            self.assertEqual(sync_one(log.pk, post=Mock(side_effect=AssertionError("Duplicate POST")),
                                      circuit=None).status, "Success")
        with urlopen(f'{self.live_server_url}/api/enrollments/{payment["enrollment_id"]}/',
                     timeout=5) as response:
            result = json.load(response)
        self.assertEqual(result["learning_status"], "Enrolled")
        self.assertEqual(result["invoice_outcome"], "Accepted")
        self.assertEqual(MockERPInvoice.objects.count(), 1)
