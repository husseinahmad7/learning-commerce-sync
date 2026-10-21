import os
from pathlib import Path
import socket
import sys
import time

sys.path.insert(0, "/app")


def check_worker():
    from config.celery import app

    replies = app.control.ping(destination=[f"worker@{socket.gethostname()}"], timeout=1)
    if not replies:
        raise RuntimeError("Worker did not reply")


def check_beat():
    if time.time() - Path("/run/celery/heartbeat").stat().st_mtime > 15:
        raise RuntimeError("Scheduler heartbeat is stale")
    os.kill(int(Path("/run/celery/beat.pid").read_text()), 0)


try:
    {"worker": check_worker, "beat": check_beat}[sys.argv[1]]()
except Exception as error:
    print(f"Readiness failed: {type(error).__name__}")
    sys.exit(1)
