# Assignment interpretation and implementation map

## Goal

Build the backend of a flight price alert service. Users select a supported route
and an INR threshold. Alert creation must respond after saving, without doing
price lookup or notification work. A scheduler checks automatically every minute;
notifications are persisted when the price is at or below the threshold.

## Clarifications resolved from the brief

- The missing section 5.3 is POST /api/alerts/, confirmed by the flow and step 5.
- There are seven method/endpoint operations across six URL patterns: GET and POST
  share /api/alerts/.
- Registration, login, and the mock feed are public; the feed explicitly needs no
  authentication for the internal HTTP call despite the earlier "first two" wording.
- The malformed model example uses Python's actual __str__ method.
- The three required test categories are covered with real API/task tests rather
  than assertions that only compare literal numbers.
- Scenario 13 is a database verification, not a new notifications endpoint.
- A notification means writing NotificationLog; no actual flight booking, email,
  SMS delivery, frontend, prediction, or third-party price integration is required.

## Requirement traceability

| Requirement | Implementation |
|---|---|
| Built-in Django User plus the three specified models | pulse/models.py |
| Profile creation signal and AppConfig.ready() wiring | pulse/signals.py, pulse/apps.py |
| Public registration and authenticate()-based login | pulse/views.py, pulse/serializers.py |
| JWT protection by default | config/settings/base.py |
| Alert ownership from request.user | AlertListCreateView.perform_create() |
| User-scoped get_queryset() | AlertListCreateView.get_queryset() |
| Soft delete, manual owner check, concealed foreign IDs | AlertDeactivateView.delete() |
| Four random mock routes, unknown route returns 404 | pulse/prices.py, FlightPriceView |
| Custom role-based BasePermission | pulse/permissions.py |
| Summary aggregate() and values().annotate() counts | AdminSummaryView |
| Two shared tasks and async .delay() dispatch | pulse/tasks.py |
| Internal requests.get with query params and response.json | check_prices() |
| Automatic 60-second schedule | CELERY_BEAT_SCHEDULE in base.py |
| PostgreSQL and Redis together | docker-compose.yml |
| base/local/production settings and ignored secrets | config/settings/, .env.example, .gitignore |
| Required business tests | pulse/tests.py (26 tests) |
| All 13 Postman scenarios | postman/PulseNotify.postman_collection.json |
| Three-process setup and PGAdmin verification | README.md |
| Full live integration verification | manage.py smoke_test, docs/VALIDATION.md |

## Additional reliability decisions

Prices are compared using Decimal. Each route is fetched once per checking run.
The HTTP timeout bounds feed calls; an unavailable or malformed feed does not stop
other routes. PostgreSQL row locking and a single transaction prevent duplicate
notification writes and serialize cancellation with notification handling.
Only the explicit profile role grants admin API access. Registration cannot choose
a role, and alert creation cannot choose an owner or initial status.
