# Learning Commerce Sync

A reference application for a course portal, single sign-on, and reliable asynchronous checkout. It combines a **Next.js course portal** with a **Django/DRF commerce service**. PostgreSQL keeps durable work; Redis and Celery deliver it. Sample purchases, learners, invoices, and enrollments are fictional.

## Run locally

Requirements: Docker with Compose, and Node.js 22 or newer for the portal. Start Django, PostgreSQL, Redis, Celery Worker and Beat:

~~~sh
MOCK_ERP_RANDOM_FAILURES=false docker compose up --build --wait
~~~

Django listens on loopback at port 18001. It seeds PY101 at 99.00. To exercise the commerce flow without SSO:

~~~sh
curl -fsS http://127.0.0.1:18001/api/checkout/ \
  -H 'Content-Type: application/json' \
  -d '{"student_email":"ada@example.test","student_name":"Ada Demo","course_code":"PY101","amount_paid":"99.00","transaction_id":"demo-001"}'
# Substitute the enrollment_id returned above:
curl -fsS http://127.0.0.1:18001/api/enrollments/1/
docker compose exec -T web python manage.py invoice_history 1
~~~

Start the portal in another terminal:

~~~sh
cd portal
npm ci
npm run dev
~~~

Open http://127.0.0.1:3000. The public catalog works without external services. Follow the [identity and learning setup](deploy/identity-and-learning.md) for OIDC sign-in and a link into your Open edX course. To try the simulated purchase from the signed-in portal, set `DEMO_COMMERCE_ORIGIN=http://127.0.0.1:18001`. The button uses a fictional payment notice, not a gateway or card. It displays ERP invoice, mock tax-reporting, and enrollment states separately. Use only local test data.

## How it fits together

| Concern | Design |
| --- | --- |
| Public portal | Next.js owns the public catalog and test checkout. Open edX owns the player and authoring interfaces; this repository does not fork or replace them. |
| Navigation | Deep links send the browser to a configured LMS origin. A consistent brand and reciprocal link are deployment work. A reverse proxy is optional routing, not an authentication system. |
| Single sign-on | Two clients of one identity provider: portal and LMS. The portal uses OIDC authorization code flow with PKCE, state, nonce, and verified ID-token signatures. Each app owns its own session. |
| Reliable delivery | Checkout records the payment snapshot, pending invoice, and learning delivery in one PostgreSQL commit. Beat and Celery process each lane independently. Replayed payment IDs produce the same checkout, and a changed payload with the same ID is rejected. |
| Invoice recovery | An attempt and pessimistic `Unknown` outcome are written before sending. A lost response triggers lookup rather than blind replay. A separate mock ZATCA reporting stage starts only after ERP acceptance. It records rejection or reconciles an unknown outcome without duplicating the report; approaching deadlines are exposed in the status API. |

Django and the simulated invoice endpoint use a shared physical PostgreSQL instance in this demo, but the invoice HTTP request and enrollment delivery commit independently. The mocks show delivery contracts and failure behavior; they do **not** connect to a real payment processor, production LMS, ERPNext, or tax authority. A real connector needs provider-specific idempotency, reconciliation, account mapping, authorization and monitoring. This is not a tax compliance implementation.

See [Failure scenarios and the operator runbook](deploy/commerce-recovery.md) for retry semantics and limits.

## Test and inspect

~~~sh
docker compose exec -T web python manage.py makemigrations --check --dry-run
docker compose exec -T web python manage.py test --noinput -v 2
docker compose logs -f worker beat

cd portal
npm ci
npm test
npm run build
npm run test:integration
~~~

Portal integration checks start the built Next.js server with a local synthetic identity provider and exercise the signed callback (including invalid state and a bad signature), session, logout, course-link handoff, and demo purchase receipt. Set `COMMERCE_E2E_ORIGIN=http://127.0.0.1:18001` to exercise the same built UI path against running Django and real background workers. CI runs both modes. The automated test provider is synthetic; live LMS setup and verification are separate. The [static portfolio site](docs/index.html) can be served with `python3 -m http.server --directory docs 8000`; its outage walkthrough is illustrative and needs no backend.

Run `docker compose down` to stop the demo. If you upgraded a prior checkout, the new default Compose project and database name are intentional. They do not move or delete old volumes. Back up and migrate any wanted data separately.

For an isolated file-secret demo, run `python3 scripts/hardened.py up --build -d --wait`; it uses a separate Compose project and port 18000. The public demo is not production-ready. Keep the service on loopback, protect backups and secret files, and use authenticated/authorized APIs, TLS, least-privilege roles, and production secret management for a deployment. See [edge rate-limiting notes](deploy/edge-rate-limiting.md).
