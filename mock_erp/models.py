from django.db import models


class MockERPStudent(models.Model):
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=200)


class MockERPInvoice(models.Model):
    transaction_id = models.CharField(max_length=100, unique=True)
    student = models.ForeignKey(MockERPStudent, on_delete=models.PROTECT)
    payload = models.JSONField()
