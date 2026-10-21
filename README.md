# Academic Sync

A small Django/DRF checkout service with PostgreSQL, Redis, Celery and a separate Beat scheduler. All sample data and ERP invoices are fictional.
i didnt folliow all of the `django style guide` for this approach.

## Run

With Docker and Compose available, start the reviewer demo:

~~~sh
docker compose up --build
~~~

Plain docker compose up also builds the app from this checkout. No .env file, side script or Compose override is needed.

The demo uses test credentials and listens on 127.0.0.1:18001. Migrations and sample course PY101 (99.00) are created automatically. In another terminal:

~~~sh
curl -s http://127.0.0.1:18001/api/checkout/ \
  -H 'Content-Type: application/json' \
  -d '{"student_email":"ada@example.test","student_name":"Ada Demo","course_code":"PY101","amount_paid":"99.00","transaction_id":"demo-001"}'

# Replace 1 with enrollment_id from the response:
curl -s http://127.0.0.1:18001/api/enrollments/1/
~~~

or copy the data and use postman.
## What happens

Checkout saves the enrollment and its invoice snapshot together, then returns 200. It never contacts Redis or ERP, and web can start while Redis is down. Beat schedules the dispatcher every two seconds; a worker scans pending rows and publishes sync tasks. PostgreSQL remains the source of work if publishing fails or Redis loses messages.

The database makes transaction_id unique and gives each enrollment one sync log. Identical replays return the same enrollment; changed checkout details with that ID return 409. IDs accept 1–100 ASCII letters, digits, dots, underscores, colons or hyphens; invalid IDs return 400.

Workers lock each sync row with SELECT FOR UPDATE SKIP LOCKED. Retries use the saved name, email, course and amount even if those records change later. Connection errors, HTTP 500 and malformed replies get five retries with jittered backoff capped at 30 seconds. The chosen due date is saved before publishing a retry. retry_count counts failed attempts, including the final sixth attempt.

The worker-side circuit breaker uses atomic shared Redis state. Three consecutive failed completions open it for 15 seconds. One leased half-open probe can test recovery; epochs reject stale probe results. Open circuits or unavailable breaker storage defer Pending work without consuming retry budget. Redis restart resets this advisory state, while pending invoices remain in PostgreSQL.

The mock ERP commits an actual, separate HTTP request. Its unique invoice key returns the same invoice after a lost reply. A real ERP must offer equivalent idempotency; local locks cannot guarantee exactly one remote effect after a timeout. Failed rows keep their payload and last error.

~~~sh
docker compose exec web python manage.py retry_sync 1
~~~

## Test and inspect

~~~sh
docker compose exec -T web python manage.py test -v 2
docker compose logs -f worker beat
docker compose down
~~~

Tests use real PostgreSQL and Redis, including separate-process breaker probes, checkout races, duplicate tasks, lost replies and broker failures. Task fault tests run eagerly; live checks exercise the queue.

The two-stage image pins its base digest and all runtime dependencies with hashes. App processes run as UID 10001 with root-owned code, a read-only root filesystem, no capabilities and no new privileges. Only bounded tmpfs paths are writable; PostgreSQL data stays on its writable named volume. Beat's scheduler metadata is temporary, while invoice work is durable.

Worker and Beat are separate services using the same image. Keep exactly one Beat per schedule in production; scale workers independently. Worker readiness uses a targeted broker ping, Beat uses a scheduler heartbeat, and web readiness checks only PostgreSQL.

demo mode has dummy defaults, the API/mock route has no authentication, and the database uses its bootstrap role. Production needs managed secrets/rotation, least-privilege database roles, access control and TLS. Services bind only loopback. Rate limiting is documented at the deployment edge in deploy/edge-rate-limiting.md; no gateway or application limiter is running here.

## Optional file-secret setup

~~~sh
python3 scripts/hardened.py up --build -d --wait
~~~

It uses compose.hardened.yml on 127.0.0.1:18000, generates project secrets once in .secrets/, and saves a private database backup in .state/ before changing the database password. Existing values and data are reused. Keep both directories out of Git and images. Compose secrets are read-only local file mounts, not a secret manager; production should use managed secrets and rotation.

PS: I used podman, not docker for this setup.
