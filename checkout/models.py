from django.db import models
from django.utils import timezone


class Student(models.Model):
    name = models.CharField(max_length=200)
    email = models.EmailField(unique=True)


class Course(models.Model):
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=200)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(price__gte=0), name="course_price_nonnegative")
        ]


class Enrollment(models.Model):
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="enrollments")
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="enrollments")
    transaction_id = models.CharField(max_length=100, unique=True)


class ERPSyncLog(models.Model):
    class Status(models.TextChoices):
        PENDING = "Pending"
        SUCCESS = "Success"
        FAILED = "Failed"

    enrollment = models.OneToOneField(Enrollment, on_delete=models.PROTECT, related_name="sync_log")
    status = models.CharField(max_length=7, choices=Status.choices, default=Status.PENDING)
    retry_count = models.PositiveIntegerField(default=0)
    payload = models.JSONField()
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_error = models.CharField(max_length=500, blank=True)
    class Outcome(models.TextChoices):
        PENDING = "Pending"
        UNKNOWN = "Unknown"
        REJECTED = "Rejected"
        ACCEPTED = "Accepted"
        EXHAUSTED = "Exhausted"

    invoice_outcome = models.CharField(max_length=9, choices=Outcome.choices, default=Outcome.PENDING)
    created_at = models.DateTimeField(default=timezone.now)
    alert_at = models.DateTimeField(null=True, blank=True)
    erp_invoice_id = models.CharField(max_length=100, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "next_attempt_at"], name="checkout_status_due_idx")]


class EnrollmentOutbox(models.Model):
    """A PaymentConfirmed record for the independent learning consumer."""

    enrollment = models.OneToOneField(Enrollment, on_delete=models.PROTECT, related_name="delivery")
    created_at = models.DateTimeField(default=timezone.now)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    attempts = models.PositiveIntegerField(default=0)
    delivered_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=200, blank=True)

    class Meta:
        indexes = [models.Index(fields=["delivered_at", "next_attempt_at"], name="enroll_outbox_due_idx")]


class LearningEnrollment(models.Model):
    """Local simulation of an LMS-side idempotent entitlement."""

    student = models.ForeignKey(Student, on_delete=models.PROTECT)
    course = models.ForeignKey(Course, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["student", "course"], name="learning_one_entitlement"),
        ]


class InvoiceAttempt(models.Model):
    log = models.ForeignKey(ERPSyncLog, on_delete=models.PROTECT, related_name="attempts")
    action = models.CharField(max_length=12)
    outcome = models.CharField(max_length=20)
    occurred_at = models.DateTimeField(default=timezone.now)
    detail = models.CharField(max_length=200, blank=True)
