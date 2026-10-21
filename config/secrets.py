import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def read_secret(name: str, *, required: bool, default: str = "") -> str:
    filename = os.environ.get(f"{name}_FILE")
    if filename:
        try:
            value = Path(filename).read_text().strip()
        except OSError:
            raise ImproperlyConfigured(f"Cannot read {name}_FILE") from None
        if not value:
            raise ImproperlyConfigured(f"{name}_FILE is empty")
        return value
    if required:
        raise ImproperlyConfigured(f"{name}_FILE is required in hardened mode")
    return os.environ.get(name, default)
