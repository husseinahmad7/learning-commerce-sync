from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest.mock import patch

from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from checkout.erp import ERPResponse
from checkout.models import Enrollment, ERPSyncLog, Student
from checkout.tasks import sync_enrollment

from .helpers import PAYMENT, make_course, make_pending


@override_settings(CIRCUIT_ENABLED=False)
class PostgreSQLConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql", "These races must run on real PostgreSQL.")
        make_course()

    def race_checkouts(self, payments):
        barrier = Barrier(len(payments))

        def checkout(payment):
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                response = APIClient().post("/api/checkout/", payment, format="json")
                return response.status_code, response.data
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=len(payments)) as pool:
            return list(pool.map(checkout, payments))

    def assert_identical_race(self):
        responses = self.race_checkouts([PAYMENT.copy() for _ in range(8)])
        self.assertEqual([status for status, _ in responses], [200] * 8)
        self.assertEqual(len({body["enrollment_id"] for _, body in responses}), 1)
        self.assertEqual(sum(body["created"] for _, body in responses), 1)
        self.assertEqual(Student.objects.count(), 1)
        self.assertEqual(Enrollment.objects.count(), 1)
        self.assertEqual(ERPSyncLog.objects.count(), 1)

    def test_eight_identical_requests_for_a_new_student(self):
        self.assert_identical_race()

    def test_eight_identical_requests_for_an_existing_student(self):
        Student.objects.create(email=PAYMENT["student_email"], name=PAYMENT["student_name"])
        self.assert_identical_race()

    def test_conflicting_requests_race_without_overwriting_invoice_data(self):
        responses = self.race_checkouts([PAYMENT.copy(), {**PAYMENT, "amount_paid": "1.00"}])
        self.assertEqual(sorted(status for status, _ in responses), [200, 409])
        self.assertEqual(Enrollment.objects.count(), 1)
        self.assertEqual(ERPSyncLog.objects.count(), 1)

    def test_duplicate_workers_skip_the_locked_sync_row(self):
        log = make_pending()
        entered, release = Event(), Event()

        def slow_erp(payload):
            entered.set()
            if not release.wait(timeout=10):
                raise AssertionError("Test did not release ERP request")
            return ERPResponse(200, "MOCK-INV-1")

        def run_task():
            connections.close_all()
            try:
                return sync_enrollment.run(log.pk)
            finally:
                connections.close_all()

        with patch("checkout.tasks.post_to_erp", side_effect=slow_erp) as erp:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(run_task)
                try:
                    self.assertTrue(entered.wait(timeout=5))
                    self.assertEqual(pool.submit(run_task).result(timeout=5), "skipped")
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=5), "Success")
            self.assertEqual(erp.call_count, 1)
        log.refresh_from_db()
        self.assertEqual(log.status, "Success")
