from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
import time
from unittest.mock import Mock, patch
from uuid import uuid4

from django.conf import settings
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from redis import ConnectionError as RedisConnectionError, Redis

from checkout.circuit import CircuitBreaker, CircuitOpen
from checkout.erp import ERPResponse
from checkout.tasks import sync_enrollment

from .circuit_worker import claim_probe
from .helpers import make_course, make_pending


class CircuitTests(TransactionTestCase):
    def setUp(self):
        make_course()
        self.client = Redis.from_url(settings.CIRCUIT_REDIS_URL, decode_responses=True)
        self.key = f"test:apptech:circuit:{uuid4().hex}"

    def tearDown(self):
        self.client.delete(self.key)
        self.client.close()
        super().tearDown()

    def breaker(self, **options):
        return CircuitBreaker(self.client, self.key, threshold=1, cooldown=0.02, lease=0.1, **options)

    def open_circuit(self, breaker):
        breaker.record(breaker.acquire(), success=False)

    def test_separate_worker_processes_share_one_half_open_probe(self):
        breaker = CircuitBreaker(self.client, self.key, threshold=1, cooldown=0.02, lease=5)
        self.open_circuit(breaker)
        time.sleep(0.04)
        with ProcessPoolExecutor(max_workers=4, mp_context=get_context("spawn")) as pool:
            results = list(pool.map(claim_probe, [(settings.CIRCUIT_REDIS_URL, self.key)] * 8))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(None), 7)

    def test_stale_closed_success_cannot_close_an_open_circuit(self):
        breaker = CircuitBreaker(self.client, self.key, threshold=1, cooldown=5, lease=5)
        late_success, failure = breaker.acquire(), breaker.acquire()
        breaker.record(failure, success=False)
        self.assertFalse(breaker.record(late_success, success=True))
        with self.assertRaises(CircuitOpen):
            breaker.acquire()

    def test_expired_probe_cannot_override_a_new_probe(self):
        breaker = self.breaker()
        self.open_circuit(breaker)
        time.sleep(0.04)
        old = breaker.acquire()
        time.sleep(0.12)
        current = breaker.acquire()
        self.assertFalse(breaker.record(old, success=True))
        with self.assertRaises(CircuitOpen):
            breaker.acquire()
        self.assertTrue(breaker.record(current, success=True))
        self.assertFalse(breaker.acquire().probe)

    @override_settings(CIRCUIT_ENABLED=True)
    def test_open_circuit_defers_without_using_retry_budget_then_recovers(self):
        breaker = CircuitBreaker(self.client, self.key, threshold=1, cooldown=0.5, lease=5)
        self.open_circuit(breaker)
        log = make_pending()
        original = log.payload
        with patch("checkout.tasks.get_breaker", return_value=breaker):
            with patch("checkout.tasks.post_to_erp", return_value=ERPResponse(200, "MOCK-INV-RECOVERED")) as post:
                self.assertEqual(sync_enrollment.apply(args=[log.pk], throw=True).result, "deferred")
                post.assert_not_called()
                log.refresh_from_db()
                self.assertEqual(log.status, "Pending")
                self.assertEqual(log.retry_count, 0)
                self.assertEqual(log.payload, original)
                self.assertGreater(log.next_attempt_at, timezone.now())
                time.sleep(0.6)
                self.assertEqual(sync_enrollment.apply(args=[log.pk], throw=True).result, "Success")
                self.assertEqual(post.call_count, 1)

    def test_breaker_backend_outage_defers_without_contacting_erp(self):
        client = Mock()
        client.eval.side_effect = RedisConnectionError("unavailable")
        breaker = CircuitBreaker(client, self.key)
        log = make_pending()
        with patch("checkout.tasks.get_breaker", return_value=breaker):
            with patch("checkout.tasks.post_to_erp") as post:
                self.assertEqual(sync_enrollment.apply(args=[log.pk], throw=True).result, "deferred")
                post.assert_not_called()
        log.refresh_from_db()
        self.assertEqual(log.retry_count, 0)
        self.assertEqual(log.status, "Pending")

    def test_breaker_update_outage_does_not_undo_a_successful_sync(self):
        client = Mock()
        client.eval.side_effect = [[1, 0, 0], RedisConnectionError("unavailable")]
        breaker = CircuitBreaker(client, self.key)
        log = make_pending()
        with patch("checkout.tasks.get_breaker", return_value=breaker):
            with patch("checkout.tasks.post_to_erp", return_value=ERPResponse(200, "MOCK-INV-SAVED")):
                self.assertEqual(sync_enrollment.apply(args=[log.pk], throw=True).result, "Success")
        log.refresh_from_db()
        self.assertEqual(log.erp_invoice_id, "MOCK-INV-SAVED")
        self.assertEqual(log.status, "Success")
