from checkout.models import Course
from checkout.serializers import CheckoutSerializer
from checkout.services import create_checkout

PAYMENT = {
    "student_email": "ada@example.test",
    "student_name": "Ada Demo",
    "course_code": "PY101",
    "amount_paid": "99.00",
    "transaction_id": "test-payment-001",
}


def make_course():
    return Course.objects.create(code="PY101", name="Python Basics", price="99.00")


def make_pending(**changes):
    serializer = CheckoutSerializer(data={**PAYMENT, **changes})
    serializer.is_valid(raise_exception=True)
    enrollment, _ = create_checkout(serializer.validated_data)
    return enrollment.sync_log
