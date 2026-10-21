import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="Student",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=200)),
                ("email", models.EmailField(max_length=254, unique=True)),
            ],
        ),
        migrations.CreateModel(
            name="Course",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=50, unique=True)),
                ("name", models.CharField(max_length=200)),
                ("price", models.DecimalField(decimal_places=2, max_digits=10)),
            ],
            options={"constraints": [models.CheckConstraint(condition=models.Q(price__gte=0), name="course_price_nonnegative")]},
        ),
        migrations.CreateModel(
            name="Enrollment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("transaction_id", models.CharField(max_length=100, unique=True)),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="enrollments", to="checkout.course")),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="enrollments", to="checkout.student")),
            ],
        ),
        migrations.CreateModel(
            name="ERPSyncLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("Pending", "Pending"), ("Success", "Success"), ("Failed", "Failed")], default="Pending", max_length=7)),
                ("retry_count", models.PositiveIntegerField(default=0)),
                ("payload", models.JSONField()),
                ("next_attempt_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_error", models.CharField(blank=True, max_length=500)),
                ("erp_invoice_id", models.CharField(blank=True, max_length=100)),
                ("enrollment", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="sync_log", to="checkout.enrollment")),
            ],
            options={"indexes": [models.Index(fields=["status", "next_attempt_at"], name="checkout_status_due_idx")]},
        ),
    ]
