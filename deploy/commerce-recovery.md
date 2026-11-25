# Commerce recovery runbook

Checkout acknowledges a fictional, already-confirmed payment. In one database transaction it creates the local checkout, snapshots the invoice payload, and creates the pending learning outbox item. It does not depend on the queue being available. Celery Beat scans durable work; Redis is transport and advisory breaker state rather than the system of record. The learning consumer uses a unique learner/course record and can finish while invoice work is still pending.

| State | Meaning and action |
| --- | --- |
| Pending | No confirmed send. Retry recoverable failures with bounded backoff. Inspect retry count and latest outcome. |
| Unknown | The worker may have submitted the invoice. Reconcile against the authoritative **mock** lookup before another submission. A previously committed invoice is recorded as accepted, not reissued. If lookup is unavailable, do not assume the invoice is absent. |
| Rejected | Definitive response or payload conflict. Do not resubmit the same document. An authorized billing operator would fix and submit a corrected document via the real provider's workflow, which the simulator does not perform. |
| Accepted | An invoice ID has been confirmed. The delivery is complete. |
| Exhausted | Bounded retry budget ran out without successful submission. Resolve the outage first, then use the operator retry if appropriate. An exhausted reconciliation preserves `Unknown`. |

Use `invoice_history <enrollment_id>` to see attempt kind, start/completion and outcome without dumping the invoice payload. Use `retry_sync <enrollment_id>` only for a failed run; it keeps an unknown invoice on the reconciliation path and rejects unchanged rejected records. The scan flags items nearing a **demo** reporting window (23 hours from creation) and definitive failures; the flag is visible in `invoice_alert_at`. No emails or compliance alerts leave the demo. Actual reporting and clearance mandates depend on invoice type and integration; use the provider's documented rules.

To reproduce known paths rather than wait for random failures, run `python manage.py test checkout.tests.test_delivery` inside the web container. Cases cover known outage, lost acknowledgement, duplicate processing, crashed attempt, provider rejection, approaching deadline, and learning delivery. The container's default random simulator is useful for casual exploration; disable it for a predictable successful flow.

If Redis restarts, Beat will rediscover persisted work. If the worker dies after committing the attempt but before receiving a response, the next eligible run reconciles it. If the actual invoicing service is only reachable through the ERP and the ERP goes down, a separate queue cannot submit invoices on its own. A real independent invoicing service, if appropriate, needs explicit authorization, its own audited credentials and a clear operating runbook.

## Exercise the mock reporting authority

The demo adds a separate reporting stage **after** simulated ERP acceptance, while enrollment proceeds independently. The report uses a fictional invoice reference and amount, not learner details. Status exposes separate `invoice_outcome`, `tax_status`, `tax_receipt_id`, `tax_alert_at`, and `learning_status` fields. Pre-upgrade ERP successes are not submitted retroactively. The reporting paths and statuses are local simulator contracts; they are not an emulation of ZATCA protocol, signing, accreditation, or a real ZATCA invoice-status endpoint.

Use a disposable Compose project and a fresh checkout transaction ID for each exercise:

~~~sh
MOCK_ERP_RANDOM_FAILURES=false MOCK_TAX_SCENARIO=lost_ack \
  docker compose -p learning-commerce-sync-tax-qa up --build --wait
~~~

- **`accept`** (default): a confirmed mock receipt and `Accepted`.
- **`reject`**: `Rejected`, held for correction, no receipt and no automatic resubmission.
- **`lost_ack`**: the mock authority stores the report, the worker records `Unknown`, then a separate lookup confirms the original receipt. There is one authority record, not two.
- **ERP unavailable**: reporting waits for the ERP receipt; the learner can still enroll independently. A stalled or ambiguous item can be inspected with `invoice_history <enrollment_id>`.

After a new demo checkout, use the status endpoint and the attempt journal from the [README](../README.md). To switch scenarios in this disposable project, change the environment then recreate app services; use a fresh transaction ID. Existing data belongs to its original scenario. When finished: `docker compose -p learning-commerce-sync-tax-qa down --volumes`.

For real vendor work, use [ERPNext integration documentation](https://docs.frappe.io/erpnext/integrating-erpnext-with-other-applications) and [ZATCA developer guidance](https://zatca.gov.sa/en/E-Invoicing/Introduction/Guidelines/Documents/DEVELOPER-PORTAL-MANUAL.pdf). Provider sandbox credentials, formally correct documents and approved signing are outside this demo.
