from decimal import Decimal

from rest_framework import serializers


class CheckoutSerializer(serializers.Serializer):
    student_email = serializers.EmailField()
    student_name = serializers.CharField(max_length=200)
    course_code = serializers.CharField(max_length=50)
    amount_paid = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
    transaction_id = serializers.RegexField(
        regex=r"\A[A-Za-z0-9._:-]+\Z",
        max_length=100,
        trim_whitespace=False,
        error_messages={"invalid": "Use ASCII letters, digits, dots, underscores, colons or hyphens only."},
    )

    def validate_student_email(self, value):
        return value.lower()

    def validate_course_code(self, value):
        return value.upper()
