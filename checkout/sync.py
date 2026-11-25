from dataclasses import dataclass
from datetime import datetime, timedelta
import random
from typing import Callable

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .circuit import CircuitBreaker, CircuitOpen
from .erp import ERPResponse, UnknownInvoiceOutcome, lookup_mock_invoice
from .models import ERPSyncLog, InvoiceAttempt, TaxDelivery


@dataclass
class SyncResult:
    status: str
    retry_at: datetime | None = None
    error: Exception | None = None


def _backoff(log):
    cap = min(settings.SYNC_RETRY_BASE_SECONDS * 2 ** min(log.retry_count - 1, 10), 30)
    log.next_attempt_at = timezone.now() + timedelta(seconds=random.uniform(cap / 2, cap))


def _accept(log, invoice_id):
    log.status = ERPSyncLog.Status.SUCCESS
    log.invoice_outcome = ERPSyncLog.Outcome.ACCEPTED
    log.erp_invoice_id = invoice_id
    log.last_error = ""
    invoice = log.payload["invoice"]
    TaxDelivery.objects.get_or_create(
        invoice=log,
        defaults={"payload": {
            "transaction_id": invoice["transaction_id"],
            "invoice_id": invoice_id,
            "amount": invoice["amount_paid"],
        }},
    )


def sync_one(
    sync_id: int,
    *,
    post: Callable[[dict], ERPResponse],
    circuit: CircuitBreaker | None,
    lookup: Callable[[dict], ERPResponse] = lookup_mock_invoice,
) -> SyncResult:
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
        reconciling = log.invoice_outcome == ERPSyncLog.Outcome.UNKNOWN
        action = "lookup" if reconciling else "submit"
        # Commit a pessimistic outcome before the network boundary. If the
        # process dies after the remote commit, the next delivery must look up.
        # A short lease covers the gap between this commit and the next row lock.
        log.invoice_outcome = ERPSyncLog.Outcome.UNKNOWN
        log.next_attempt_at = timezone.now() + timedelta(seconds=20)
        lease = log.next_attempt_at
        log.save(update_fields=["invoice_outcome", "next_attempt_at"])
        InvoiceAttempt.objects.create(log=log, action=action, outcome="Started")

    with transaction.atomic():
        log = ERPSyncLog.objects.select_for_update().get(pk=sync_id)
        if log.next_attempt_at != lease or log.status != ERPSyncLog.Status.PENDING:
            return SyncResult("skipped")
        retry_error = None
        audit_outcome = ""
        try:
            response = (lookup if reconciling else post)(log.payload)
            if 200 <= response.status_code < 300 and response.invoice_id:
                _accept(log, response.invoice_id)
                audit_outcome = "Accepted"
            elif reconciling and response.status_code == 404:
                # This branch relies specifically on the simulator's authoritative lookup.
                # It does NOT apply to a vendor's unknown/eventually consistent status.
                log.invoice_outcome = ERPSyncLog.Outcome.PENDING
                log.last_error = ""
                log.next_attempt_at = timezone.now()
                audit_outcome = "NotFound"
            elif reconciling:
                raise UnknownInvoiceOutcome(f"Mock status lookup returned HTTP {response.status_code}")
            elif response.status_code >= 500:
                raise ConnectionError(f"Mock ERP returned HTTP {response.status_code}")
            elif response.status_code in {400, 409, 422}:
                log.status = ERPSyncLog.Status.FAILED
                log.invoice_outcome = ERPSyncLog.Outcome.REJECTED
                log.last_error = f"Mock ERP rejected the payload: HTTP {response.status_code}"
                log.alert_at = log.alert_at or timezone.now()
                audit_outcome = "Rejected"
            else:
                raise UnknownInvoiceOutcome(f"Mock ERP returned HTTP {response.status_code}")
            if circuit:
                circuit.record(permit, success=True)
        except (ConnectionError, TimeoutError, UnknownInvoiceOutcome) as error:
            if circuit:
                circuit.record(permit, success=False)
            unknown = reconciling or isinstance(error, (TimeoutError, UnknownInvoiceOutcome))
            log.retry_count += 1
            log.last_error = str(error)[:500]
            audit_outcome = "Unknown" if unknown else "Unavailable"
            log.invoice_outcome = (
                ERPSyncLog.Outcome.UNKNOWN if unknown else ERPSyncLog.Outcome.PENDING
            )
            if log.retry_count > settings.SYNC_MAX_RETRIES:
                log.status = ERPSyncLog.Status.FAILED
                if not unknown:
                    log.invoice_outcome = ERPSyncLog.Outcome.EXHAUSTED
                log.alert_at = log.alert_at or timezone.now()
            else:
                _backoff(log)
                retry_error = error
        log.save(update_fields=[
            "status", "invoice_outcome", "alert_at", "retry_count", "last_error",
            "erp_invoice_id", "next_attempt_at",
        ])
        InvoiceAttempt.objects.create(
            log=log, action=action, outcome=audit_outcome, detail=log.last_error[:200],
        )
    return SyncResult(log.status, log.next_attempt_at if retry_error else None, retry_error)
