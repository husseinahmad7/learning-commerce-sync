from redis import Redis

from checkout.circuit import CircuitBreaker, CircuitOpen


def claim_probe(arguments):
    url, key = arguments
    client = Redis.from_url(url, decode_responses=True)
    try:
        permit = CircuitBreaker(client, key, threshold=1, cooldown=0.02, lease=5).acquire()
        return permit.probe
    except CircuitOpen:
        return None
    finally:
        client.close()
