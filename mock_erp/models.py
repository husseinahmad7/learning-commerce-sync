from django.db import models


class MockERPStudent(models.Model):
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=200)


class MockERPInvoice(models.Model):
    transaction_id = models.CharField(max_length=100, unique=True)
    student = models.ForeignKey(MockERPStudent, on_delete=models.PROTECT)
    payload = models.JSONField()


class MockTaxReport(models.Model):
    """Fictional tax authority's records; independent of mock ERP rows."""

    transaction_id = models.CharField(max_length=100, unique=True)
    payload = models.JSONField()
    accepted = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)
