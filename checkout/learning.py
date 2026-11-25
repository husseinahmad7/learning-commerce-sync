from datetime import timedelta

from django.db import OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from .models import EnrollmentOutbox, LearningEnrollment


def deliver_enrollment(delivery_id):
    """Model a separate consumer; the simulator currently shares the physical DB."""
    try:
        with transaction.atomic():
            row = (
                EnrollmentOutbox.objects.select_for_update(skip_locked=True, of=("self",))
                .select_related("enrollment")
                .filter(pk=delivery_id, delivered_at__isnull=True, next_attempt_at__lte=timezone.now())
                .first()
            )
            if not row:
                return "skipped"
            LearningEnrollment.objects.get_or_create(
                student_id=row.enrollment.student_id, course_id=row.enrollment.course_id,
            )
            row.delivered_at = timezone.now()
            row.attempts += 1
            row.last_error = ""
            row.save(update_fields=["delivered_at", "attempts", "last_error"])
            return "enrolled"
    except OperationalError:
        # The enrollment transaction rolled back. A duplicate delivery is harmless.
        EnrollmentOutbox.objects.filter(pk=delivery_id, delivered_at__isnull=True).update(
            attempts=F("attempts") + 1, last_error="Temporary enrollment storage failure",
            next_attempt_at=timezone.now() + timedelta(seconds=15),
        )
        return "deferred"
