from decimal import Decimal

from django.core.management.base import BaseCommand

from checkout.models import Course


class Command(BaseCommand):
    help = "Add the fictional demo course if it is missing."

    def handle(self, *args, **options):
        Course.objects.get_or_create(
            code="PY101", defaults={"name": "Python Basics", "price": Decimal("99.00")}
        )
