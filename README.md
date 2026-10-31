# PulseNotify

A Django REST Framework backend for monitoring flight prices. Users create private
price alerts; Celery Beat checks prices every 60 seconds and queues a notification
task when a price is at or below the user's threshold. A notification is a database
log entry, as required by the assignment; email and SMS are outside its scope.

This is a standalone project at `D:\airtribe\pythonc22\PulseNotify`. Run all
commands below **from this folder**.

See [assignment analysis](docs/ASSIGNMENT.md) for the requirement mapping and
[validation results](docs/VALIDATION.md) for the completed checks.

## Quick start with Docker (recommended, including Windows)

Install Docker Desktop with Linux containers and start its engine. Then:

```powershell
cd D:\airtribe\pythonc22\PulseNotify
Copy-Item .env.example .env
```

Edit `.env`: replace `SECRET_KEY`, `POSTGRES_PASSWORD`, and
`PGADMIN_DEFAULT_PASSWORD`. Generate a secret with
`python -c "import secrets; print(secrets.token_urlsafe(50))"` if Python is installed.
Keep `DJANGO_SETTINGS_MODULE=config.settings.local` for this assignment. If port
5432 is already used, set `POSTGRES_PORT=55432`; the prepared local `.env` uses
55432 to avoid your other course projects.

```powershell
docker compose up --build -d
docker compose ps
docker compose logs -f worker beat
```

Compose starts PostgreSQL and Redis, applies migrations, starts the API on
http://localhost:8000, and runs separate worker and Beat processes. Internal
hostnames are overridden automatically (`postgres`, `redis`, `web`). Only run one
Beat scheduler. Stop services with `docker compose down`; database volumes persist.

```powershell
docker compose exec web python manage.py test
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py set_role YOUR_USERNAME admin
```

The `UserProfile.role` controls `/api/admin/summary/`. Django's `is_staff` and
`is_superuser` control the separate Django admin at `/admin/`; they do not grant
the API admin role automatically. Public registration always creates role `user`.

## Local development: three terminals

Use Python 3.12. From this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit the secrets in .env before continuing.
docker compose up -d postgres redis
python manage.py migrate
python manage.py runserver
```

In terminal 2, from this folder with the same virtual environment activated:

```powershell
celery -A config worker --loglevel=info
```

In terminal 3:

```powershell
celery -A config beat --loglevel=info
```

Celery does not officially support Windows workers. Prefer Docker for the complete
workflow. For a Windows course demo, `celery -A config worker --loglevel=info
--pool=solo` can be used locally. Keep the API running: the worker calls its HTTP
price endpoint through `requests.get(..., params=..., timeout=5)`.

On macOS/Linux use `python3 -m venv .venv`, `source .venv/bin/activate`, and
`cp .env.example .env`; the remaining commands are the same.

## API contract

Protected requests use `Authorization: Bearer <access>`. Money in alert responses
is a decimal string. Price feed responses contain an integer in INR. Lists are
unpaginated, including `[]` for users without alerts.

| Method | Endpoint | Access | Result |
|---|---|---|---|
| POST | `/api/auth/register/` | Public | 201, username, access JWT, role |
| POST | `/api/auth/login/` | Public | 200, username, access JWT, role |
| POST | `/api/alerts/` | JWT | 201, new active alert |
| GET | `/api/alerts/` | JWT | 200, current user's alerts |
| DELETE | `/api/alerts/<id>/` | JWT, owner | 200, `{"status":"inactive"}` |
| GET | `/api/flights/price/?route=DEL-BOM` | Public | 200, route and random price |
| GET | `/api/admin/summary/` | JWT, profile role admin | 200, platform statistics |

Register or log in with:

```json
{"username":"alice","password":"FlightAlerts!925","email":"alice@example.com"}
```

Create an alert with:

```json
{"origin":"DEL","destination":"BOM","threshold_price":"4500.00"}
```

The owner always comes from `request.user`. Supplied `user`, `user_id`, `status`,
and `role` cannot grant ownership, change initial status, or elevate a role.
Passwords use Django validation and hashing. Duplicate usernames return 400;
invalid credentials return 401. Missing/invalid JWTs return 401; regular users
receive 403 on the summary. Missing and foreign alerts both return 404 on DELETE.
DELETE preserves the database row and can be repeated.

| Supported route | Mock price range (INR, inclusive) |
|---|---|
| DEL-BOM | 3000–7000 |
| BLR-HYD | 1500–4000 |
| DEL-BLR | 4000–9000 |
| BOM-GOA | 2000–5000 |

Alert creation normalizes airport codes to uppercase, rejects unsupported routes,
and requires a positive threshold with at most two decimal places. The mock feed
uses the exact supplied route and returns 404 for missing or unknown routes.

## Background flow and design

```mermaid
sequenceDiagram
    participant User
    participant API
    participant DB as PostgreSQL
    participant Beat
    participant Worker
    participant Redis
    User->>API: POST /api/alerts/ + JWT
    API->>DB: Save active alert
    API-->>User: 201 immediately
    Beat->>Redis: check_prices every 60 seconds
    Redis->>Worker: Run check_prices
    Worker->>DB: Get distinct active routes
    Worker->>API: GET /api/flights/price/?route=DEL-BOM
    API-->>Worker: route and random price
    Worker->>Redis: send_notification.delay(id, price) when price <= threshold
    Redis->>Worker: Run send_notification
    Worker->>DB: Lock alert; write NotificationLog; mark triggered atomically
```

The API never calls the feed or notification task during alert creation. Each
check fetches one price per distinct active route and compares using Decimal.
HTTP failures, timeouts, and malformed prices are logged and skipped so other
routes continue; the next scheduled check tries again. Both tasks use
`@shared_task`; notification dispatch uses `.delay()`.

`send_notification` rechecks status and threshold under a PostgreSQL row lock.
Duplicate queued deliveries and deliveries for deactivated/missing alerts do not
write notifications. The log and triggered status commit in one transaction.
Deactivation takes the same row lock. Overlapping check jobs may enqueue duplicate
tasks, but the notification write is idempotent. Feed prices are simulated and
random; this project does not predict prices or book flights.

The admin summary uses database aggregates and grouped counts. `top_routes` returns
the five most common routes across all statuses, with deterministic tie ordering.

## Postman: all 13 submission scenarios

Import `postman/PulseNotify.postman_collection.json` into Postman. The collection
has no real secrets. Set `base_url` if needed. Run requests 01–11 in order; request
01 generates a unique username and stores its access token. Request 06 creates a
DEL-BOM alert with threshold 7000 so every mock price qualifies. Request 08 creates
and deactivates a separate disposable alert, leaving request 06's alert available
for the background demonstration.

Before request 12, create an admin account (register it via the API, or use
`createsuperuser`), then run `python manage.py set_role USERNAME admin` (prefix with
`docker compose exec web` when containerized). Set collection variables
`admin_username` and `admin_password` locally in Postman. Request 12 logs in with
these and stores its token. Do not export populated secrets for submission.

Before request 13, wait at least 60–90 seconds with API, worker, and Beat running.
It asserts that the platform contains a notification. **Also do the required
PGAdmin check below** to verify the row belongs to the alert from request 06;
the aggregate count alone does not establish that. This is the manual database
scenario from the rubric, not an extra notification API.

## PGAdmin and notification evidence

```powershell
docker compose --profile tools up -d pgadmin
```

Open http://localhost:5050 and log in using your `.env` PGAdmin credentials.
Register a server: host `postgres`, port `5432`, maintenance database and username
from `.env`, password `POSTGRES_PASSWORD`. The host is `postgres` because PGAdmin
is inside Compose. A desktop PGAdmin uses `localhost` instead.

In Query Tool, replace `123` with the `alert_id` saved by Postman request 06:

```sql
SELECT a.id, a.origin, a.destination, a.threshold_price, a.status,
       n.triggered_price, n.message, n.notified_at
FROM pulse_pricealert AS a
JOIN pulse_notificationlog AS n ON n.alert_id = a.id
WHERE a.id = 123;
```

Expect one log, a price <= 7000, and alert status `triggered`. Wait another minute
and rerun: the same alert should still have one log. Save screenshots of this row,
worker/Beat logs, passing Postman requests, and passing tests for the submission.

## Tests and checks

With PostgreSQL running and `.env` configured:

```powershell
python manage.py test
python manage.py check
python manage.py makemigrations --check --dry-run
```

The PostgreSQL role needs permission to create a test database. Compose's initialized
role has it. Tests create and destroy an isolated test database, not your app tables.
For tests without Docker/PostgreSQL:

```powershell
python manage.py test --settings=config.settings.test
python manage.py check --settings=config.settings.test
```

The isolated test settings use SQLite in memory. They verify actual API behavior
with signed JWTs and call the real task functions with mocked HTTP/broker boundaries.
Tests cover below/equal/above thresholds, log contents and status transitions,
duplicate deliveries, canceled alerts, rollback, ownership, authentication, role
permissions, grouped statistics, and invalid feed responses. These tests do not
replace the live Redis/Beat/PostgreSQL demonstration. CI additionally runs the
full live integration command against the Compose services.

## Settings, security, and deployment

`config/settings/base.py` contains shared Django, DRF, JWT, database, and Celery
settings. `local.py` enables DEBUG; `production.py` disables DEBUG and enables HTTPS
and secure cookies. `.env` is ignored by Git and excluded from Docker images.
All application secrets and database credentials come from the environment.

For deployment, use `DJANGO_SETTINGS_MODULE=config.settings.production`, a generated
secret, explicit public `ALLOWED_HOSTS`, managed PostgreSQL/Redis credentials, and
an HTTPS endpoint for `FLIGHT_PRICE_URL`. Terminate TLS securely and configure the
proxy correctly. The provided Compose file is for the local course demonstration;
its health check assumes local HTTP. Access JWTs expire after 60 minutes; log in
again to obtain a new one. No token refresh endpoint is part of this assignment.

Dependencies are pinned in `requirements.txt`; `requirements.lock` additionally
pins transitive packages. Docker uses the lock file for repeatable installs.

## Submission

Create a dedicated PulseNotify GitHub repository and submit its URL. Include the
source, migrations, Dockerfile/Compose, requirements, Postman JSON, and this README.
Never include `.env`, virtual environments, database dumps, or populated tokens.
