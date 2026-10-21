from dataclasses import dataclass
from functools import lru_cache
import logging
from uuid import uuid4

from django.conf import settings
from redis import Redis, RedisError

logger = logging.getLogger(__name__)

# Redis time, atomic transitions and epochs fence late results from older calls.
ACQUIRE = """
local k = KEYS[1]
local t = redis.call('TIME')
local now = t[1]*1000 + math.floor(t[2]/1000)
local epoch = tonumber(redis.call('HGET',k,'epoch') or '0')
local until_at = tonumber(redis.call('HGET',k,'open_until') or '0')
if until_at > now then return {0,until_at-now,0} end
if until_at > 0 then
  local lease = tonumber(redis.call('HGET',k,'lease_until') or '0')
  if lease > now then return {0,lease-now,0} end
  epoch = epoch + 1
  redis.call('HSET',k,'epoch',epoch,'probe_token',ARGV[1],'lease_until',now+ARGV[2])
  redis.call('EXPIRE',k,3600)
  return {1,epoch,1}
end
redis.call('HSETNX',k,'epoch',epoch)
redis.call('EXPIRE',k,3600)
return {1,epoch,0}
"""

RECORD = """
local k = KEYS[1]
local epoch = tonumber(redis.call('HGET',k,'epoch') or '-1')
if epoch ~= tonumber(ARGV[1]) then return 0 end
local t = redis.call('TIME')
local now = t[1]*1000 + math.floor(t[2]/1000)
local probe = ARGV[3] == '1'
if probe then
  if redis.call('HGET',k,'probe_token') ~= ARGV[2] then return 0 end
  if tonumber(redis.call('HGET',k,'lease_until') or '0') <= now then return 0 end
elseif tonumber(redis.call('HGET',k,'open_until') or '0') > 0 then
  return 0
end
if ARGV[4] == '1' then
  redis.call('HSET',k,'failures',0)
  if probe then
    redis.call('HSET',k,'open_until',0,'epoch',epoch+1)
    redis.call('HDEL',k,'probe_token','lease_until')
  end
else
  local failures = redis.call('HINCRBY',k,'failures',1)
  if probe or failures >= tonumber(ARGV[5]) then
    redis.call('HSET',k,'open_until',now+ARGV[6],'epoch',epoch+1)
    redis.call('HDEL',k,'probe_token','lease_until')
  end
end
redis.call('EXPIRE',k,3600)
return 1
"""


class CircuitOpen(Exception):
    def __init__(self, retry_after: float):
        self.retry_after = max(0.01, retry_after)
        super().__init__("ERP circuit is unavailable; durable work is deferred")


@dataclass(frozen=True)
class Permit:
    token: str
    epoch: int
    probe: bool


class CircuitBreaker:
    def __init__(self, client: Redis, key: str, *, threshold=3, cooldown=15, lease=20):
        self.client, self.key = client, key
        self.threshold, self.cooldown, self.lease = threshold, cooldown, lease

    def acquire(self) -> Permit:
        token = uuid4().hex
        try:
            allowed, value, probe = self.client.eval(ACQUIRE, 1, self.key, token, int(self.lease * 1000))
        except RedisError:
            raise CircuitOpen(5) from None
        if not allowed:
            raise CircuitOpen(value / 1000)
        return Permit(token, value, bool(probe))

    def record(self, permit: Permit, *, success: bool) -> bool:
        try:
            return bool(self.client.eval(
                RECORD, 1, self.key, permit.epoch, permit.token, int(permit.probe),
                int(success), self.threshold, int(self.cooldown * 1000),
            ))
        except RedisError:
            # An advisory breaker update must never undo an invoice result saved in PostgreSQL.
            logger.warning("ERP circuit update unavailable")
            return False


@lru_cache
def redis_client(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1)


def get_breaker() -> CircuitBreaker | None:
    if not settings.CIRCUIT_ENABLED:
        return None
    return CircuitBreaker(
        redis_client(settings.CIRCUIT_REDIS_URL), settings.CIRCUIT_KEY,
        threshold=settings.CIRCUIT_FAILURE_THRESHOLD,
        cooldown=settings.CIRCUIT_COOLDOWN_SECONDS,
        lease=settings.CIRCUIT_PROBE_LEASE_SECONDS,
    )
