from pathlib import Path

from celery.beat import PersistentScheduler


class HeartbeatScheduler(PersistentScheduler):
    def tick(self, *args, **kwargs):
        interval = super().tick(*args, **kwargs)
        Path("/run/celery/heartbeat").touch()
        return interval
