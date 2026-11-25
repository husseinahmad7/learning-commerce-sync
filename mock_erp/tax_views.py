"""Fictional reporting contract. This is not a ZATCA API implementation."""

from decimal import Decimal

from django.conf import settings
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import MockTaxReport


class TaxPayload(serializers.Serializer):
    transaction_id = serializers.RegexField(r"\A[A-Za-z0-9_.:-]+\Z", max_length=100)
    invoice_id = serializers.RegexField(r"\AMOCK-INV-[0-9]+\Z", max_length=100)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.00"))


def tax_result(obj):
    data = {"transaction_id": obj.transaction_id, "invoice_id": obj.payload["invoice_id"]}
    if obj.accepted:
        return Response({**data, "outcome": "Accepted", "receipt_id": f"MOCK-TAX-{obj.pk}"})
    return Response({
        **data, "outcome": "Rejected", "reason_code": "demo_validation_rejected",
    }, status=422)


@api_view(["POST"])
def report(request):
    serializer = TaxPayload(data=request.data)
    serializer.is_valid(raise_exception=True)
    payload = dict(serializer.data)
    key = payload["transaction_id"]
    if request.headers.get("Idempotency-Key") != key:
        return Response({"detail": "Idempotency-Key must match transaction_id."}, status=400)
    obj, _ = MockTaxReport.objects.get_or_create(
        transaction_id=key, defaults={"payload": payload, "accepted": settings.MOCK_TAX_SCENARIO != "reject"},
    )
    if obj.payload != payload:
        return Response({"detail": "Idempotency key reused with different data."}, status=409)
    return tax_result(obj)


@api_view(["GET"])
def status(request):
    key = request.query_params.get("transaction_id", "")
    serializer = serializers.RegexField(r"\A[A-Za-z0-9_.:-]+\Z", max_length=100)
    serializer.run_validation(key)
    obj = MockTaxReport.objects.filter(transaction_id=key).first()
    if not obj:
        return Response({"code": "report_not_recorded", "transaction_id": key}, status=404)
    return tax_result(obj)
