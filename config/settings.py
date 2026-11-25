import os

from .secrets import read_secret

APP_MODE = os.environ.get("APP_MODE", "demo")
if APP_MODE not in {"demo", "hardened"}:
    raise ValueError("APP_MODE must be demo or hardened")
SECRET_KEY = read_secret("DJANGO_SECRET_KEY", required=APP_MODE == "hardened", default="demo-only-not-a-production-secret")
DEBUG = False
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "web", "testserver"]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
INSTALLED_APPS = ["rest_framework", "checkout", "mock_erp"]
MIDDLEWARE = ["django.middleware.common.CommonMiddleware"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
STATIC_URL = "/static/"
MEDIA_URL = "/media/"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "learning_commerce"),
        "USER": os.environ.get("DB_USER", "learning_commerce"),
        "PASSWORD": read_secret("DB_PASSWORD", required=APP_MODE == "hardened", default="development-only"),
        "HOST": os.environ.get("DB_HOST", "db"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_TASK_DEFAULT_QUEUE = "erp-sync"
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "visibility_timeout": 60,
    "socket_connect_timeout": 1,
    "socket_timeout": 1,
}
CELERY_BEAT_SCHEDULE = {
    "dispatch-pending-syncs": {
        "task": "checkout.dispatch_pending",
        "schedule": 2.0,
        "options": {"expires": 10},
    }
}

MOCK_ERP_URL = os.environ.get("MOCK_ERP_URL", "http://web:8000/mock-erp/sync/")
MOCK_ERP_RANDOM_FAILURES = os.environ.get("MOCK_ERP_RANDOM_FAILURES", "true").lower() == "true"
SYNC_MAX_RETRIES = 5
SYNC_RETRY_BASE_SECONDS = 2
CIRCUIT_ENABLED = os.environ.get("CIRCUIT_ENABLED", "true").lower() == "true"
CIRCUIT_REDIS_URL = os.environ.get("CIRCUIT_REDIS_URL", CELERY_BROKER_URL)
CIRCUIT_KEY = os.environ.get("CIRCUIT_KEY", "learning_commerce:erp:circuit")
CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_COOLDOWN_SECONDS = 15
CIRCUIT_PROBE_LEASE_SECONDS = 20
