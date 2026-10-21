from decimal import Decimal
from typing import TypedDict

from django.db import transaction

from .models import Course, Enrollment, ERPSyncLog, Student


class CheckoutData(TypedDict):
    student_email: str
    student_name: str
    course_code: str
    amount_paid: Decimal
    transaction_id: str


class TransactionConflict(Exception):
    pass


class UnknownCourse(Exception):
    pass


def matches_checkout(payload: dict, data: CheckoutData) -> bool:
    return (
        payload["student"] == {"email": data["student_email"], "name": data["student_name"]}
        and payload["invoice"]["course_code"] == data["course_code"]
        and payload["invoice"]["amount_paid"] == format(data["amount_paid"], ".2f")
    )


@transaction.atomic
def create_checkout(data: CheckoutData) -> tuple[Enrollment, bool]:
    try:
        course = Course.objects.get(code=data["course_code"])
    except Course.DoesNotExist:
        raise UnknownCourse("Unknown course.") from None

    student, _ = Student.objects.get_or_create(
        email=data["student_email"], defaults={"name": data["student_name"]}
    )
    # get_or_create relies on the database UNIQUE constraint when inserts race.
    enrollment, created = Enrollment.objects.get_or_create(
        transaction_id=data["transaction_id"], defaults={"student": student, "course": course}
    )
    if created:
        ERPSyncLog.objects.create(
            enrollment=enrollment,
            payload={
                "student": {"email": data["student_email"], "name": data["student_name"]},
                "invoice": {
                    "transaction_id": data["transaction_id"],
                    "course_code": course.code,
                    "course_name": course.name,
                    "amount_paid": format(data["amount_paid"], ".2f"),
                },
            },
        )
    elif not matches_checkout(enrollment.sync_log.payload, data):
        raise TransactionConflict("This transaction_id already belongs to a different checkout.")
    return enrollment, created
