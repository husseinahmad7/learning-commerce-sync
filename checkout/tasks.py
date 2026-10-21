import logging

from celery import shared_task
from django.utils import timezone
from kombu.exceptions import OperationalError

from .erp import post_to_erp
from .circuit import get_breaker
from .models import ERPSyncLog
from .sync import sync_one

logger = logging.getLogger(__name__)


@shared_task(name="checkout.dispatch_pending")
def dispatch_pending():
    ids = list(ERPSyncLog.objects.filter(
        status=ERPSyncLog.Status.PENDING, next_attempt_at__lte=timezone.now()
    ).order_by("id").values_list("id", flat=True)[:100])
    published = 0
    for sync_id in ids:
        try:
            sync_enrollment.apply_async(args=[sync_id], retry=False)
            published += 1
        except OperationalError:
            logger.warning("Broker unavailable; sync %s remains pending", sync_id)
            break
    return published


@shared_task(bind=True, name="checkout.sync_enrollment", max_retries=5, soft_time_limit=10, time_limit=15)
def sync_enrollment(self, sync_id):
    result = sync_one(sync_id, post=post_to_erp, circuit=get_breaker())
    # The persisted deadline is shared with the dispatcher; losing this message is safe.
    if result.error:
        delay = max(0, (result.retry_at - timezone.now()).total_seconds())
        raise self.retry(exc=result.error, countdown=delay)
    return result.status
