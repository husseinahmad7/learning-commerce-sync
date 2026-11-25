import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone
from kombu.exceptions import OperationalError

from .erp import post_to_erp
from .circuit import get_breaker
from .learning import deliver_enrollment
from .models import EnrollmentOutbox, ERPSyncLog
from .sync import sync_one

logger = logging.getLogger(__name__)


@shared_task(name="checkout.dispatch_pending")
def dispatch_pending():
    ids = list(ERPSyncLog.objects.filter(
        status=ERPSyncLog.Status.PENDING, next_attempt_at__lte=timezone.now()
    ).order_by("next_attempt_at", "id").values_list("id", flat=True)[:100])
    mark_invoice_deadlines()
    published = 0
    for sync_id in ids:
        try:
            sync_enrollment.apply_async(args=[sync_id], retry=False)
            published += 1
        except OperationalError:
            logger.warning("Broker unavailable; sync %s remains pending", sync_id)
            return published
    for delivery_id in EnrollmentOutbox.objects.filter(
        delivered_at__isnull=True, next_attempt_at__lte=timezone.now(),
    ).order_by("next_attempt_at", "id").values_list("id", flat=True)[:100]:
        try:
            sync_learning.apply_async(args=[delivery_id], retry=False)
        except OperationalError:
            logger.warning("Broker unavailable; learning delivery %s remains pending", delivery_id)
            break
    return published


def mark_invoice_deadlines():
    # Demo SLA: simplified reporting is modeled at 24 h, with a one-hour lead.
    # This is an operator signal, not an assumption about a real invoice type.
    now = timezone.now()
    count = ERPSyncLog.objects.filter(
        created_at__lte=now - timedelta(hours=23), alert_at__isnull=True,
    ).exclude(invoice_outcome=ERPSyncLog.Outcome.ACCEPTED).update(alert_at=now)
    if count:
        logger.warning("%s invoice(s) require deadline attention", count)
    return count


@shared_task(name="checkout.sync_learning")
def sync_learning(delivery_id):
    return deliver_enrollment(delivery_id)


@shared_task(bind=True, name="checkout.sync_enrollment", max_retries=5, soft_time_limit=10, time_limit=15)
def sync_enrollment(self, sync_id):
    result = sync_one(sync_id, post=post_to_erp, circuit=get_breaker())
    # The persisted deadline is shared with the dispatcher; losing this message is safe.
    if result.error:
        delay = max(0, (result.retry_at - timezone.now()).total_seconds())
        raise self.retry(exc=result.error, countdown=delay)
    return result.status
