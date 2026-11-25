from datetime import timedelta
from http.client import IncompleteRead
from io import BytesIO
from unittest.mock import patch

from billiard.exceptions import SoftTimeLimitExceeded
from celery.exceptions import Retry
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from kombu.exceptions import OperationalError

from checkout.erp import ERPResponse
from checkout.models import Course, ERPSyncLog, Student
from checkout.tasks import dispatch_pending, sync_enrollment

from .helpers import make_course, make_pending


class ERPReply(BytesIO):
    status = 200


class TruncatedERPReply(ERPReply):
    def read(self, *args):
        raise IncompleteRead(b'{"invoice_id":', 20)


class TimedOutERPReply(ERPReply):
    def read(self, *args):
        raise SoftTimeLimitExceeded()


@override_settings(SYNC_RETRY_BASE_SECONDS=0, CIRCUIT_ENABLED=False)
class SyncTaskTests(TransactionTestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch("checkout.tasks.sync_learning.apply_async").start()
        make_course()
        self.log = make_pending()

    def attempt(self):
        return sync_enrollment.apply(args=[self.log.pk], throw=True).result

    def test_connection_error_and_http_500_retry_the_original_payload(self):
        original = self.log.payload
        Student.objects.update(name="Edited later")
        Course.objects.update(price="1.00")
        with patch("checkout.tasks.post_to_erp", side_effect=[ConnectionError("offline"), ERPResponse(500), ERPResponse(200, "MOCK-INV-1")]) as erp:
            for count in (1, 2):
                with self.assertRaises(Retry):
                    self.attempt()
                self.log.refresh_from_db()
                self.assertEqual(self.log.status, "Pending")
                self.assertEqual(self.log.retry_count, count)
                self.assertEqual(self.log.payload, original)
            self.assertEqual(self.attempt(), "Success")
            self.assertEqual([call.args[0] for call in erp.call_args_list], [original] * 3)
        self.log.refresh_from_db()
        self.assertEqual(self.log.erp_invoice_id, "MOCK-INV-1")
        self.assertEqual(self.log.retry_count, 2)

    def test_retry_exhaustion_preserves_failed_invoice(self):
        original = self.log.payload
        with patch("checkout.tasks.post_to_erp", side_effect=ConnectionError("offline")):
            for _ in range(5):
                with self.assertRaises(Retry):
                    self.attempt()
            self.assertEqual(self.attempt(), "Failed")
        self.log.refresh_from_db()
        self.assertEqual(self.log.retry_count, 6)
        self.assertEqual(self.log.payload, original)
        self.assertEqual(self.log.last_error, "offline")

    def assert_transport_failure_is_bounded(self, response_factory):
        original = self.log.payload
        with patch("checkout.erp.urlopen", side_effect=lambda *args, **kwargs: response_factory()):
            for _ in range(5):
                with self.assertRaises(Retry):
                    self.attempt()
            self.assertEqual(self.attempt(), "Failed")
        self.log.refresh_from_db()
        self.assertEqual(self.log.retry_count, 6)
        self.assertEqual(self.log.payload, original)
        self.assertEqual(self.log.last_error, "Mock ERP response unavailable or invalid")
        self.assertEqual(self.log.invoice_outcome, "Unknown")

    @override_settings(MOCK_ERP_RANDOM_FAILURES=False)
    def test_malformed_erp_responses_use_bounded_retries(self):
        bodies = (
            b"[]", b"null", b"{}", b"not-json",
            b'{"invoice_id":null}', b'{"invoice_id":42}',
            b'{"invoice_id":""}', b'{"invoice_id":"   "}',
            rb'{"invoice_id":"\u0000"}', rb'{"invoice_id":"\ud800"}',
            b'{"invoice_id":"' + b"x" * 101 + b'"}',
        )
        for index, body in enumerate(bodies):
            with self.subTest(response=body):
                self.log = make_pending(transaction_id=f"malformed-reply-{index}")
                self.assert_transport_failure_is_bounded(lambda: ERPReply(body))

    @override_settings(MOCK_ERP_RANDOM_FAILURES=False)
    def test_truncated_erp_response_uses_bounded_retries(self):
        self.assert_transport_failure_is_bounded(TruncatedERPReply)

    @override_settings(MOCK_ERP_RANDOM_FAILURES=False)
    def test_oversized_erp_reply_uses_bounded_retries(self):
        self.assert_transport_failure_is_bounded(lambda: ERPReply(b"x" * 65537))

    @override_settings(MOCK_ERP_RANDOM_FAILURES=False)
    def test_soft_time_limit_uses_bounded_retry_path(self):
        self.assert_transport_failure_is_bounded(TimedOutERPReply)

    def test_permanent_rejection_is_retained_as_failed(self):
        with patch("checkout.tasks.post_to_erp", return_value=ERPResponse(409)):
            self.assertEqual(self.attempt(), "Failed")
        self.log.refresh_from_db()
        self.assertIn("409", self.log.last_error)

    def test_completed_task_delivery_does_not_call_erp_again(self):
        with patch("checkout.tasks.post_to_erp", return_value=ERPResponse(200, "MOCK-INV-1")) as erp:
            self.assertEqual(self.attempt(), "Success")
            self.assertEqual(self.attempt(), "skipped")
            self.assertEqual(erp.call_count, 1)

    def test_future_retry_is_not_run_early(self):
        ERPSyncLog.objects.filter(pk=self.log.pk).update(next_attempt_at=timezone.now() + timedelta(minutes=1))
        with patch("checkout.tasks.post_to_erp") as erp:
            self.assertEqual(self.attempt(), "skipped")
            erp.assert_not_called()

    @override_settings(SYNC_RETRY_BASE_SECONDS=2)
    def test_jittered_deadline_is_persisted_and_respected_by_new_deliveries(self):
        with patch("checkout.sync.random.uniform", return_value=1.5):
            with patch("checkout.tasks.post_to_erp", side_effect=ConnectionError("offline")) as post:
                with self.assertRaises(Retry):
                    self.attempt()
                self.log.refresh_from_db()
                deadline = self.log.next_attempt_at
                self.assertGreater(deadline, timezone.now())
                self.assertEqual(self.attempt(), "skipped")
                with patch.object(sync_enrollment, "apply_async") as publish:
                    self.assertEqual(dispatch_pending.run(), 0)
                    publish.assert_not_called()
                self.log.refresh_from_db()
                self.assertEqual(self.log.next_attempt_at, deadline)
                self.assertEqual(post.call_count, 1)

    def test_broker_publish_failure_leaves_pending_work_for_the_next_sweep(self):
        with patch.object(sync_enrollment, "apply_async", side_effect=OperationalError("broker down")):
            self.assertEqual(dispatch_pending.run(), 0)
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, "Pending")
        with patch.object(sync_enrollment, "apply_async") as publish:
            self.assertEqual(dispatch_pending.run(), 1)
            publish.assert_called_once_with(args=[self.log.pk], retry=False)

    def test_lost_retry_publication_keeps_the_saved_retry_state(self):
        with patch("checkout.tasks.post_to_erp", side_effect=ConnectionError("ERP offline")):
            with patch.object(sync_enrollment, "retry", side_effect=OperationalError("broker down")):
                with self.assertRaises(OperationalError):
                    sync_enrollment.run(self.log.pk)
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, "Pending")
        self.assertEqual(self.log.retry_count, 1)
        with patch.object(sync_enrollment, "apply_async") as publish:
            self.assertEqual(dispatch_pending.run(), 1)
            publish.assert_called_once_with(args=[self.log.pk], retry=False)

    def test_manual_retry_preserves_payload(self):
        original = self.log.payload
        ERPSyncLog.objects.filter(pk=self.log.pk).update(status="Failed", retry_count=6)
        call_command("retry_sync", self.log.enrollment_id)
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, "Pending")
        self.assertEqual(self.log.retry_count, 0)
        self.assertEqual(self.log.payload, original)

    def test_manual_retry_rejects_already_successful_sync(self):
        ERPSyncLog.objects.filter(pk=self.log.pk).update(status="Success")
        with self.assertRaises(CommandError):
            call_command("retry_sync", self.log.enrollment_id)
