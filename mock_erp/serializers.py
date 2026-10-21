from decimal import Decimal

from rest_framework import serializers


class StudentPayloadSerializer(serializers.Serializer):
    email = serializers.EmailField()
    name = serializers.CharField(max_length=200)


class InvoicePayloadSerializer(serializers.Serializer):
    transaction_id = serializers.CharField(max_length=100)
    course_code = serializers.CharField(max_length=50)
    course_name = serializers.CharField(max_length=200)
    amount_paid = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))


class SyncPayloadSerializer(serializers.Serializer):
    student = StudentPayloadSerializer()
    invoice = InvoicePayloadSerializer()
