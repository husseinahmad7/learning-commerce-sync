from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from checkout.models import ERPSyncLog


class Command(BaseCommand):
    help = "Retry a failed enrollment sync using its saved payload."

    def add_arguments(self, parser):
        parser.add_argument("enrollment_id", type=int)

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            log = ERPSyncLog.objects.select_for_update().get(enrollment_id=options["enrollment_id"])
        except ERPSyncLog.DoesNotExist:
            raise CommandError("Enrollment sync not found.")
        if log.status != ERPSyncLog.Status.FAILED:
            raise CommandError("Only Failed syncs can be retried manually.")
        if log.invoice_outcome == ERPSyncLog.Outcome.REJECTED:
            raise CommandError("Rejected invoices need a corrected document; unchanged retries are disabled.")
        if log.invoice_outcome != ERPSyncLog.Outcome.UNKNOWN:
            log.invoice_outcome = ERPSyncLog.Outcome.PENDING
        log.status = ERPSyncLog.Status.PENDING
        log.retry_count = 0
        log.next_attempt_at = timezone.now()
        log.last_error = ""
        log.save(update_fields=["status", "retry_count", "next_attempt_at", "last_error", "invoice_outcome"])
        self.stdout.write(f'Enrollment {options["enrollment_id"]} queued for retry.')
