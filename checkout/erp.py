import json
import random
from dataclasses import dataclass
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from billiard.exceptions import SoftTimeLimitExceeded
from django.conf import settings


@dataclass(frozen=True)
class ERPResponse:
    status_code: int
    invoice_id: str = ""


def post_to_erp(payload):
    outcome = random.random() if settings.MOCK_ERP_RANDOM_FAILURES else 1.0
    if outcome < 0.2:
        raise ConnectionError("Simulated ERP connection failure")
    if outcome < 0.4:
        return ERPResponse(500)

    request = Request(
        settings.MOCK_ERP_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": payload["invoice"]["transaction_id"],
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=3) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("Mock ERP response exceeds 64 KiB")
            body = json.loads(raw)
            invoice_id = body["invoice_id"]
            if (
                not isinstance(invoice_id, str)
                or not invoice_id.strip()
                or not invoice_id.isprintable()
                or len(invoice_id) > 100
            ):
                raise ValueError("Invalid mock ERP invoice ID")
            status = response.status
    except HTTPError as error:
        return ERPResponse(error.code)
    except (URLError, TimeoutError, HTTPException, ValueError, KeyError, TypeError, SoftTimeLimitExceeded) as error:
        raise ConnectionError("Mock ERP response unavailable or invalid") from error

    if outcome < 0.5:
        # The separate HTTP request committed its invoice, but the worker lost the reply.
        raise ConnectionError("Simulated lost ERP acknowledgement after commit")
    return ERPResponse(status, invoice_id)
