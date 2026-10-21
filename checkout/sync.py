from dataclasses import dataclass
from datetime import datetime, timedelta
import random
from typing import Callable

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .circuit import CircuitBreaker, CircuitOpen
from .erp import ERPResponse
from .models import ERPSyncLog


@dataclass
class SyncResult:
    status: str
    retry_at: datetime | None = None
    error: Exception | None = None


def sync_one(sync_id: int, *, post: Callable[[dict], ERPResponse], circuit: CircuitBreaker | None) -> SyncResult:
    with transaction.atomic():
        log = ERPSyncLog.objects.select_for_update(skip_locked=True).filter(pk=sync_id).first()
        if not log or log.status != ERPSyncLog.Status.PENDING or log.next_attempt_at > timezone.now():
            return SyncResult("skipped")
        permit = None
        if circuit:
            try:
                permit = circuit.acquire()
            except CircuitOpen as error:
                log.next_attempt_at = timezone.now() + timedelta(seconds=error.retry_after)
                log.save(update_fields=["next_attempt_at"])
                return SyncResult("deferred")
        retry_error = None
        try:
            response = post(log.payload)
            if response.status_code >= 500:
                raise ConnectionError(f"Mock ERP returned HTTP {response.status_code}")
            if response.status_code >= 400:
                log.status = ERPSyncLog.Status.FAILED
                log.last_error = f"Mock ERP rejected the payload: HTTP {response.status_code}"
            else:
                log.status = ERPSyncLog.Status.SUCCESS
                log.erp_invoice_id = response.invoice_id
                log.last_error = ""
            if circuit:
                circuit.record(permit, success=True)
        except (ConnectionError, TimeoutError) as error:
            if circuit:
                circuit.record(permit, success=False)
            log.retry_count += 1
            log.last_error = str(error)[:500]
            if log.retry_count > settings.SYNC_MAX_RETRIES:
                log.status = ERPSyncLog.Status.FAILED
            else:
                cap = min(settings.SYNC_RETRY_BASE_SECONDS * 2 ** (log.retry_count - 1), 30)
                delay = random.uniform(cap / 2, cap)
                log.next_attempt_at = timezone.now() + timedelta(seconds=delay)
                retry_error = error
        log.save(update_fields=["status", "retry_count", "last_error", "erp_invoice_id", "next_attempt_at"])
    return SyncResult(log.status, log.next_attempt_at if retry_error else None, retry_error)
