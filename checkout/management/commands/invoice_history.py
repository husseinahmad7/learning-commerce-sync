from django.core.management.base import BaseCommand, CommandError

from checkout.models import ERPSyncLog


class Command(BaseCommand):
    help = "Show a demo invoice's attempt history without printing the checkout payload."

    def add_arguments(self, parser):
        parser.add_argument("enrollment_id", type=int)

    def handle(self, *args, **options):
        try:
            log = ERPSyncLog.objects.get(enrollment_id=options["enrollment_id"])
        except ERPSyncLog.DoesNotExist:
            raise CommandError("Invoice workflow not found.") from None
        self.stdout.write(
            f"Invoice outcome: {log.invoice_outcome}; "
            f"attention since: {log.alert_at.isoformat() if log.alert_at else 'not flagged'}"
        )
        for attempt in log.attempts.order_by("occurred_at", "id"):
            self.stdout.write(f"{attempt.occurred_at.isoformat()} {attempt.action} {attempt.outcome}")
        tax = getattr(log, "tax_delivery", None)
        if tax:
            self.stdout.write(f"Demo tax status: {tax.status}; held for review: {tax.held}")
            for attempt in tax.attempts.order_by("occurred_at", "id"):
                self.stdout.write(f"{attempt.occurred_at.isoformat()} {attempt.action} {attempt.outcome}")
