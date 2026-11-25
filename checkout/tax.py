"""HTTP boundary and durable state machine for the fictional tax reporting demo."""

import json
import socket
from datetime import timedelta
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from billiard.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import TaxAttempt, TaxDelivery


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


local_http = build_opener(ProxyHandler({}), RejectRedirects())


class UnconfirmedReport(Exception):
    pass


def _body(response):
    raw = response.read(4097)
    if len(raw) > 4096:
        raise ValueError("Report response too large")
    return json.loads(raw)


def _accepted(document, payload):
    receipt = document.get("receipt_id")
    if (
        document.get("outcome") != "Accepted"
        or document.get("transaction_id") != payload["transaction_id"]
        or document.get("invoice_id") != payload["invoice_id"]
        or not isinstance(receipt, str)
        or not receipt.startswith("MOCK-TAX-")
        or not receipt.removeprefix("MOCK-TAX-").isascii()
        or not receipt.removeprefix("MOCK-TAX-").isdigit()
        or len(receipt) > 100
    ):
        raise ValueError("Invalid demo receipt")
    return "Accepted", receipt


def tax_api(payload, *, lookup):
    endpoint = urlsplit(settings.MOCK_TAX_URL)
    if (endpoint.scheme != "http" or endpoint.hostname not in {"web", "127.0.0.1", "localhost", "::1"}
            or endpoint.username or endpoint.password or not endpoint.path.endswith("/mock-tax/report/")
            or endpoint.query or endpoint.fragment):
        raise UnconfirmedReport("Only the fictional reporting contract is supported")
    key = payload["transaction_id"]
    if lookup:
        path = endpoint.path.removesuffix("report/") + "status/"
        url = urlunsplit((endpoint.scheme, endpoint.netloc, path, urlencode({"transaction_id": key}), ""))
        request = Request(url)
    else:
        request = Request(
            settings.MOCK_TAX_URL, data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json", "Idempotency-Key": key},
        )
    try:
        with local_http.open(request, timeout=3) as response:
            result = _accepted(_body(response), payload)
    except HTTPError as error:
        try:
            with error:
                document = _body(error)
            if lookup and error.code == 404 and document == {"code": "report_not_recorded", "transaction_id": key}:
                return "NotFound", ""
            if error.code == 422 and document == {
                "transaction_id": key, "invoice_id": payload["invoice_id"],
                "outcome": "Rejected", "reason_code": "demo_validation_rejected",
            }:
                return "Rejected", ""
        except (OSError, HTTPException, ValueError, TypeError, SoftTimeLimitExceeded):
            pass
        raise UnconfirmedReport("Unconfirmed demo reporting response") from error
    except URLError as error:
        if isinstance(error.reason, (ConnectionRefusedError, socket.gaierror)):
            raise ConnectionError("Demo reporting connection not established") from error
        raise UnconfirmedReport("Demo reporting reply unavailable") from error
    except (ConnectionRefusedError, socket.gaierror) as error:
        raise ConnectionError("Demo reporting connection not established") from error
    except (OSError, HTTPException, ValueError, TypeError, AttributeError, SoftTimeLimitExceeded) as error:
        raise UnconfirmedReport("Demo reporting reply unavailable or invalid") from error
    if settings.MOCK_TAX_SCENARIO == "lost_ack" and not lookup:
        raise UnconfirmedReport("Simulated lost acknowledgement after reporting commit")
    return result


def process_tax(delivery_id):
    with transaction.atomic():
        delivery = TaxDelivery.objects.select_for_update(skip_locked=True).filter(
            pk=delivery_id, held=False, status__in=[TaxDelivery.Status.PENDING, TaxDelivery.Status.UNKNOWN],
            next_attempt_at__lte=timezone.now(),
        ).first()
        if delivery is None:
            return "skipped"
        lookup = delivery.status == TaxDelivery.Status.UNKNOWN
        lease = timezone.now() + timedelta(seconds=20)
        delivery.status = TaxDelivery.Status.UNKNOWN
        delivery.next_attempt_at = lease
        delivery.save(update_fields=["status", "next_attempt_at"])
        action = "lookup" if lookup else "report"
        TaxAttempt.objects.create(delivery=delivery, action=action, outcome="Started")

    with transaction.atomic():
        delivery = TaxDelivery.objects.select_for_update().get(pk=delivery_id)
        if delivery.next_attempt_at != lease or delivery.held or delivery.status != TaxDelivery.Status.UNKNOWN:
            return "skipped"
        try:
            outcome, receipt = tax_api(delivery.payload, lookup=lookup)
            if outcome == "NotFound":
                delivery.status = TaxDelivery.Status.PENDING
                delivery.next_attempt_at = timezone.now()
                delivery.last_error = ""
            elif outcome == "Accepted":
                delivery.status = TaxDelivery.Status.ACCEPTED
                delivery.receipt_id = receipt
                delivery.last_error = ""
            else:
                delivery.status = TaxDelivery.Status.REJECTED
                delivery.alert_at = delivery.alert_at or timezone.now()
                delivery.held = True
                delivery.last_error = "Demo report rejected: correction needed"
        except (ConnectionError, UnconfirmedReport) as error:
            ambiguous = lookup or isinstance(error, UnconfirmedReport)
            delivery.status = TaxDelivery.Status.UNKNOWN if ambiguous else TaxDelivery.Status.PENDING
            delivery.last_error = str(error)
            delivery.retry_count += 1
            delivery.next_attempt_at = timezone.now() + timedelta(
                seconds=min(settings.SYNC_RETRY_BASE_SECONDS * 2 ** min(delivery.retry_count - 1, 8), 30),
            )
            outcome = "Unknown" if ambiguous else "Unavailable"
            if delivery.retry_count > settings.SYNC_MAX_RETRIES:
                delivery.alert_at = delivery.alert_at or timezone.now()
                delivery.held = True
                if not ambiguous:
                    delivery.status = TaxDelivery.Status.ATTENTION
        delivery.save(update_fields=[
            "status", "next_attempt_at", "last_error", "receipt_id", "retry_count", "alert_at", "held",
        ])
        TaxAttempt.objects.create(delivery=delivery, action=action, outcome=outcome)
        return delivery.status
