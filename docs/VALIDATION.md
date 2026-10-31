# Validation record

Validated on 1 November 2026 (Asia/Kolkata) from the standalone PulseNotify project.

| Check | Result |
|---|---|
| Django tests, PostgreSQL in Docker | 26 passed |
| Django tests, isolated SQLite settings | 26 passed |
| Newman / Postman collection | 13 scenarios, 15 HTTP requests including setup, 20 assertions, zero failures |
| Ruff lint and formatting | Passed, 30 Python files formatted |
| Migration consistency | No changes detected |
| Live API + Redis + Celery worker + Beat + PostgreSQL | Passed |

The live smoke command exercised all 13 rubric scenarios and waited for Beat rather
than manually calling the checking task. Alert 1 (DEL-BOM, threshold 7000.00)
triggered at 5035.00 and produced NotificationLog 1.

The final passing Postman run created alert 5. Beat queued its notification, and
PostgreSQL contained this exact joined row:

| Alert | Route | Threshold | Status | Notification | Triggered price |
|---|---|---|---|---|---|
| 5 | DEL-BOM | 7000.00 | triggered | 3 | 6204.00 |

A subsequent scheduled check found no active routes for these completed alerts.
The database still contained exactly one notification per triggered alert.
Database rows were checked directly with psql. PGAdmin screenshot evidence remains
a manual submission step; see the README SQL. The automated checks did not use
the PGAdmin UI.

## Repeat verification

```powershell
docker compose exec -T web python manage.py test
docker compose exec -T web python manage.py smoke_test
python manage.py test --settings=config.settings.test
python manage.py makemigrations --check --dry-run --settings=config.settings.test
```

Import the public collection from `postman/` into Postman and follow the README.
The private Newman runtime report is under `.runtime/postman-report.json`, which
is ignored by Git because it contains test credentials and access tokens. Do not
upload that report or the local `.env`.

The source and local Git repository are independent of SubLedger. SubLedger's
working tree was left unchanged. PulseNotify has not been published to GitHub;
create a dedicated remote repository before submitting its URL.
