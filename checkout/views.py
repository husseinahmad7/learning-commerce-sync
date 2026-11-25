from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .models import Enrollment
from .serializers import CheckoutSerializer
from .services import TransactionConflict, UnknownCourse, create_checkout


def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


@api_view(["POST"])
def checkout(request):
    serializer = CheckoutSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        enrollment, created = create_checkout(serializer.validated_data)
    except UnknownCourse as error:
        raise ValidationError({"course_code": str(error)}) from None
    except TransactionConflict as error:
        return Response({"detail": str(error)}, status=409)
    # The durable pending row is picked up by Beat; checkout never contacts Redis.
    return Response({
        "enrollment_id": enrollment.pk,
        "transaction_id": enrollment.transaction_id,
        "sync_status": enrollment.sync_log.status,
        "created": created,
    })


@api_view(["GET"])
def enrollment_detail(request, pk):
    enrollment = get_object_or_404(Enrollment.objects.select_related("sync_log", "delivery"), pk=pk)
    log = enrollment.sync_log
    return Response({
        "enrollment_id": enrollment.pk,
        "transaction_id": enrollment.transaction_id,
        "sync_status": log.status,
        "invoice_outcome": log.invoice_outcome,
        "invoice_alert_at": log.alert_at,
        "learning_status": "Enrolled" if getattr(enrollment, "delivery", None) and enrollment.delivery.delivered_at else "Pending",
        "retry_count": log.retry_count,
        "erp_invoice_id": log.erp_invoice_id,
        "last_error": log.last_error,
        "payload": log.payload,
    })
