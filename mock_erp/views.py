from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import MockERPInvoice, MockERPStudent
from .serializers import SyncPayloadSerializer


@api_view(["POST"])
def sync(request):
    serializer = SyncPayloadSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    payload = dict(serializer.data)
    key = payload["invoice"]["transaction_id"]
    if request.headers.get("Idempotency-Key") != key:
        return Response({"detail": "Idempotency-Key must match transaction_id."}, status=400)

    # This HTTP request commits independently of the worker's sync-log transaction.
    with transaction.atomic():
        student, _ = MockERPStudent.objects.get_or_create(
            email=payload["student"]["email"], defaults={"name": payload["student"]["name"]}
        )
        invoice, _ = MockERPInvoice.objects.get_or_create(
            transaction_id=key, defaults={"student": student, "payload": payload}
        )
        if invoice.payload != payload:
            transaction.set_rollback(True)
            return Response({"detail": "Idempotency key reused with different data."}, status=409)
    return Response({"student_id": student.pk, "invoice_id": f"MOCK-INV-{invoice.pk}"})


@api_view(["GET"])
def invoice_lookup(request):
    """Local simulator lookup, not a claim that a real vendor offers this API."""
    key = request.query_params.get("transaction_id", "")
    if not key or len(key) > 100:
        return Response({"detail": "A transaction_id is required."}, status=400)
    invoice = MockERPInvoice.objects.filter(transaction_id=key).first()
    if invoice is None:
        return Response({"code": "invoice_not_recorded", "transaction_id": key}, status=404)
    return Response({"invoice_id": f"MOCK-INV-{invoice.pk}"})
