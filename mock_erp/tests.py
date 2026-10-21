import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from urllib.request import Request, urlopen

from celery.exceptions import Retry
from django.test import LiveServerTestCase, override_settings

from checkout.tasks import sync_enrollment
from checkout.tests.helpers import make_course, make_pending
from mock_erp.models import MockERPInvoice, MockERPStudent


@override_settings(SYNC_RETRY_BASE_SECONDS=0, CIRCUIT_ENABLED=False, MOCK_ERP_RANDOM_FAILURES=True)
class MockERPHTTPTests(LiveServerTestCase):
    def setUp(self):
        make_course()
        self.log = make_pending()

    def send_to_mock(self, payload):
        request = Request(
            f"{self.live_server_url}/mock-erp/sync/",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Idempotency-Key": payload["invoice"]["transaction_id"]},
        )
        with urlopen(request, timeout=10) as response:
            return response.status, json.load(response)

    def test_eight_simultaneous_http_requests_create_one_erp_invoice(self):
        barrier = Barrier(8)

        def send(_):
            barrier.wait(timeout=10)
            return self.send_to_mock(self.log.payload)

        with ThreadPoolExecutor(max_workers=8) as pool:
            replies = list(pool.map(send, range(8)))
        self.assertEqual([status for status, _ in replies], [200] * 8)
        self.assertEqual(len({body["invoice_id"] for _, body in replies}), 1)
        self.assertEqual(MockERPStudent.objects.count(), 1)
        self.assertEqual(MockERPInvoice.objects.count(), 1)

    def test_invoice_survives_lost_reply_and_retry_reuses_its_id(self):
        with override_settings(MOCK_ERP_URL=f"{self.live_server_url}/mock-erp/sync/"):
            with patch("checkout.erp.random.random", return_value=0.45):
                with self.assertRaises(Retry):
                    sync_enrollment.apply(args=[self.log.pk], throw=True)
            self.log.refresh_from_db()
            self.assertEqual(self.log.status, "Pending")
            invoice = MockERPInvoice.objects.get()
            self.assertEqual(invoice.payload, self.log.payload)
            with patch("checkout.erp.random.random", return_value=0.9):
                result = sync_enrollment.apply(args=[self.log.pk], throw=True)
            self.assertEqual(result.result, "Success")
        self.log.refresh_from_db()
        self.assertEqual(MockERPInvoice.objects.count(), 1)
        self.assertEqual(self.log.erp_invoice_id, f"MOCK-INV-{invoice.pk}")
