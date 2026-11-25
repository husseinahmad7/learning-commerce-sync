from django.urls import path

from checkout import views
from mock_erp.views import invoice_lookup, sync as mock_sync

urlpatterns = [
    path("api/health/", views.health),
    path("api/checkout/", views.checkout),
    path("api/enrollments/<int:pk>/", views.enrollment_detail),
    path("mock-erp/sync/", mock_sync),
    path("mock-erp/invoice-status/", invoice_lookup),
]
