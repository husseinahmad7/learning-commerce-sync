import json
import random
import socket
from dataclasses import dataclass
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from billiard.exceptions import SoftTimeLimitExceeded
from django.conf import settings


class UnknownInvoiceOutcome(Exception):
    """Sending might have succeeded, but we do not have a reliable reply."""


@dataclass(frozen=True)
class ERPResponse:
    status_code: int
    invoice_id: str = ""


def _read_response(request, *, lookup_key=None):
    try:
        with urlopen(request, timeout=3) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("Mock ERP response exceeds 64 KiB")
            invoice_id = json.loads(raw)["invoice_id"]
            if (
                not isinstance(invoice_id, str)
                or not invoice_id.strip()
                or not invoice_id.isprintable()
                or len(invoice_id) > 100
            ):
                raise ValueError("Invalid mock ERP invoice ID")
            return ERPResponse(response.status, invoice_id)
    except HTTPError as error:
        with error:
            if error.code >= 500:
                raise UnknownInvoiceOutcome("Mock ERP returned an ambiguous server error") from error
            if error.code == 404 and lookup_key is not None:
                try:
                    raw = error.read(4097)
                    verified = len(raw) <= 4096 and json.loads(raw) == {
                        "code": "invoice_not_recorded", "transaction_id": lookup_key,
                    }
                except (OSError, HTTPException, ValueError, TypeError, SoftTimeLimitExceeded):
                    verified = False
                if not verified:
                    raise UnknownInvoiceOutcome("Mock status lookup did not confirm invoice absence") from error
            return ERPResponse(error.code)
    except URLError as error:
        if isinstance(error.reason, (ConnectionRefusedError, socket.gaierror)):
            raise ConnectionError("Mock ERP connection could not be established") from error
        raise UnknownInvoiceOutcome("Mock ERP response unavailable or invalid") from error
    except (ConnectionRefusedError, socket.gaierror) as error:
        raise ConnectionError("Mock ERP connection could not be established") from error
    except (OSError, HTTPException, ValueError, KeyError, TypeError, SoftTimeLimitExceeded) as error:
        raise UnknownInvoiceOutcome("Mock ERP response unavailable or invalid") from error


def post_to_erp(payload):
    outcome = random.random() if settings.MOCK_ERP_RANDOM_FAILURES else 1.0
    if outcome < 0.2:
        raise ConnectionError("Simulated ERP connection failure")
    if outcome < 0.4:
        # Known pre-commit outage in this synthetic fault mode only.
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
    response = _read_response(request)
    if outcome < 0.5 and 200 <= response.status_code < 300:
        raise UnknownInvoiceOutcome("Simulated lost ERP acknowledgement after commit")
    return response


def lookup_mock_invoice(payload):
    """The mock lookup is authoritative; do not enable this against a real vendor URL."""
    endpoint = urlsplit(settings.MOCK_ERP_URL)
    if not endpoint.path.endswith("/mock-erp/sync/"):
        raise UnknownInvoiceOutcome("Invoice lookup is not configured for this endpoint")
    path = endpoint.path.removesuffix("sync/") + "invoice-status/"
    key = payload["invoice"]["transaction_id"]
    query = urlencode({"transaction_id": key})
    request = Request(urlunsplit((endpoint.scheme, endpoint.netloc, path, query, "")))
    return _read_response(request, lookup_key=key)
