import json
import socket
from datetime import timedelta
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from celery.exceptions import Retry
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone

from checkout.models import ERPSyncLog, LearningEnrollment, TaxDelivery
from checkout.tax import UnconfirmedReport, local_http, tax_api
from checkout.tasks import dispatch_pending, mark_invoice_deadlines, sync_enrollment, sync_learning, sync_tax
from mock_erp.models import MockERPInvoice, MockTaxReport

from .helpers import PAYMENT, make_course


@override_settings(CIRCUIT_ENABLED=False, MOCK_ERP_RANDOM_FAILURES=False, SYNC_RETRY_BASE_SECONDS=0)
class TaxHTTPWorkflowTests(LiveServerTestCase):
    def setUp(self):
        make_course()
        config = override_settings(
            MOCK_ERP_URL=f"{self.live_server_url}/mock-erp/sync/",
            MOCK_TAX_URL=f"{self.live_server_url}/mock-tax/report/",
            MOCK_TAX_SCENARIO="accept",
        )
        config.enable()
        self.addCleanup(config.disable)

    @override_settings(MOCK_TAX_URL="https://external.example/mock-tax/report/")
    def test_reporting_adapter_rejects_external_hosts_before_network_call(self):
        with patch.object(local_http, "open") as send:
            with self.assertRaises(UnconfirmedReport):
                tax_api({}, lookup=False)
        send.assert_not_called()

    def request(self, path, data=None, key=None):
        headers = {"Content-Type": "application/json"} if data is not None else {}
        if key:
            headers["Idempotency-Key"] = key
        request = Request(
            f"{self.live_server_url}{path}",
            data=json.dumps(data).encode() if data is not None else None,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.load(error)

    def checkout(self):
        status, result = self.request("/api/checkout/", data=PAYMENT)
        self.assertEqual(status, 200)
        status, replay = self.request("/api/checkout/", data=PAYMENT)
        self.assertEqual(status, 200)
        self.assertFalse(replay["created"])
        self.assertEqual(replay["enrollment_id"], result["enrollment_id"])
        return ERPSyncLog.objects.get(enrollment_id=result["enrollment_id"])

    def detail(self, log):
        return self.request(f"/api/enrollments/{log.enrollment_id}/")[1]

    def post_to_erp(self, invoice):
        self.assertEqual(sync_enrollment.run(invoice.pk), "Success")
        self.assertEqual(sync_enrollment.run(invoice.pk), "skipped")
        self.assertEqual(MockERPInvoice.objects.count(), 1)
        return TaxDelivery.objects.get(invoice=invoice)

    def test_payment_queues_independent_enrollment_and_tax_waits_for_erp(self):
        invoice = self.checkout()
        self.assertEqual(self.detail(invoice)["tax_status"], "WaitingForERP")
        # Reserve but don't listen on a loopback-only port: actual HTTP connect refusal.
        with socket.socket() as unavailable:
            unavailable.bind(("127.0.0.1", 0))
            port = unavailable.getsockname()[1]
            with override_settings(MOCK_ERP_URL=f"http://127.0.0.1:{port}/mock-erp/sync/"):
                with self.assertRaises(Retry):
                    sync_enrollment.apply(args=[invoice.pk], throw=True)
        self.assertEqual(MockERPInvoice.objects.count(), 0)
        self.assertFalse(TaxDelivery.objects.exists())
        delivery_id = invoice.enrollment.delivery.pk
        self.assertEqual(sync_learning.run(delivery_id), "enrolled")
        self.assertEqual(sync_learning.run(delivery_id), "skipped")
        self.assertEqual(LearningEnrollment.objects.count(), 1)
        after_outage = self.detail(invoice)
        self.assertEqual(after_outage["learning_status"], "Enrolled")
        self.assertEqual(after_outage["invoice_outcome"], "Pending")
        self.assertEqual(after_outage["tax_status"], "WaitingForERP")
        tax = self.post_to_erp(invoice)
        with patch.object(sync_tax, "apply_async") as publish:
            dispatch_pending.run()
        publish.assert_called_once_with(args=[tax.pk], retry=False)
        self.assertEqual(sync_tax.run(tax.pk), "Accepted")
        self.assertEqual(sync_tax.run(tax.pk), "skipped")
        self.assertTrue(self.detail(invoice)["tax_receipt_id"].startswith("MOCK-TAX-"))
        self.assertEqual(MockTaxReport.objects.count(), 1)
        tax.refresh_from_db()
        self.assertEqual(set(tax.payload), {"transaction_id", "invoice_id", "amount"})

    def test_authority_rejects_without_retry_or_undoing_enrollment(self):
        invoice = self.checkout()
        self.assertEqual(sync_learning.run(invoice.enrollment.delivery.pk), "enrolled")
        tax = self.post_to_erp(invoice)
        with override_settings(MOCK_TAX_SCENARIO="reject"):
            self.assertEqual(sync_tax.run(tax.pk), "Rejected")
        self.assertEqual(sync_tax.run(tax.pk), "skipped")
        detail = self.detail(invoice)
        self.assertEqual(detail["learning_status"], "Enrolled")
        self.assertEqual(detail["invoice_outcome"], "Accepted")
        self.assertEqual(detail["tax_status"], "Rejected")
        self.assertIsNotNone(detail["tax_alert_at"])
        self.assertTrue(detail["tax_held"])
        self.assertEqual(detail["tax_receipt_id"], "")
        self.assertFalse(MockTaxReport.objects.get().accepted)

    def test_committed_report_with_lost_reply_is_reconciled_without_reposting(self):
        invoice = self.checkout()
        tax = self.post_to_erp(invoice)
        with override_settings(MOCK_TAX_SCENARIO="lost_ack"):
            self.assertEqual(sync_tax.run(tax.pk), "Unknown")
            self.assertEqual(MockTaxReport.objects.count(), 1)
            self.assertEqual(self.detail(invoice)["tax_status"], "Unknown")
            self.assertEqual(sync_tax.run(tax.pk), "Accepted")
        self.assertEqual(MockTaxReport.objects.count(), 1)
        self.assertEqual(
            list(tax.attempts.order_by("id").values_list("action", "outcome")),
            [("report", "Started"), ("report", "Unknown"), ("lookup", "Started"), ("lookup", "Accepted")],
        )

    def test_waiting_tax_report_gets_a_durable_predeadline_flag(self):
        invoice = self.checkout()
        self.post_to_erp(invoice)
        ERPSyncLog.objects.filter(pk=invoice.pk).update(created_at=timezone.now() - timedelta(hours=23, minutes=1))
        self.assertEqual(mark_invoice_deadlines(), 1)
        self.assertEqual(mark_invoice_deadlines(), 0)
        detail = self.detail(invoice)
        self.assertEqual(detail["tax_status"], "Pending")
        self.assertIsNotNone(detail["tax_alert_at"])
        self.assertFalse(detail["tax_held"])

    def test_identical_reports_replay_same_receipt_and_changed_payload_conflicts(self):
        invoice = self.checkout()
        tax = self.post_to_erp(invoice)
        key = tax.payload["transaction_id"]
        self.assertEqual(self.request("/mock-tax/status/?transaction_id=" + key)[0], 404)
        first = self.request("/mock-tax/report/", tax.payload, key)
        replay = self.request("/mock-tax/report/", tax.payload, key)
        conflict = self.request("/mock-tax/report/", {**tax.payload, "amount": "88.00"}, key)
        self.assertEqual(first[0], 200)
        self.assertEqual(first, replay)
        self.assertEqual(conflict[0], 409)
        self.assertEqual(MockTaxReport.objects.count(), 1)
