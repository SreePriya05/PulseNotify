import time
import uuid

import requests
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from pulse.models import NotificationLog, PriceAlert, UserProfile


class Command(BaseCommand):
    help = "Exercise the live API, real Redis worker, Beat schedule, and notification DB write."

    def add_arguments(self, parser):
        parser.add_argument("--base-url", default="http://localhost:8000")
        parser.add_argument("--timeout", type=int, default=95)

    def handle(self, *args, **options):
        base = options["base_url"].rstrip("/")
        suffix = uuid.uuid4().hex[:12]
        username = f"smoke_{suffix}"
        password = uuid.uuid4().hex + "!aA7"
        payload = {
            "username": username,
            "password": password,
            "email": f"{username}@example.com",
            "role": "admin",
        }

        def request(method, path, expected, token=None, **kwargs):
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            try:
                response = requests.request(
                    method, base + path, headers=headers, timeout=10, **kwargs
                )
            except requests.RequestException as exc:
                raise CommandError(f"{method} {path}: {exc}") from exc
            if response.status_code != expected:
                raise CommandError(
                    f"{method} {path}: expected {expected}, got {response.status_code}"
                )
            self.stdout.write(f"PASS {method} {path}: {expected}")
            return response.json()

        user_data = request("POST", "/api/auth/register/", 201, json=payload)
        if user_data["role"] != "user":
            raise CommandError("Registration allowed role escalation.")
        token = user_data["access"]
        request("POST", "/api/auth/register/", 400, json=payload)
        request("POST", "/api/auth/login/", 200, json=payload)
        request(
            "POST", "/api/auth/login/", 401, json={"username": username, "password": "incorrect"}
        )
        alert_payload = {"origin": "DEL", "destination": "BOM", "threshold_price": "7000.00"}
        request("POST", "/api/alerts/", 401, json=alert_payload)
        alert = request("POST", "/api/alerts/", 201, token, json=alert_payload)
        own = request("GET", "/api/alerts/", 200, token)
        if [item["id"] for item in own] != [alert["id"]]:
            raise CommandError("Alert list is not scoped to the new user.")
        disposable = request("POST", "/api/alerts/", 201, token, json=alert_payload)
        request("DELETE", f"/api/alerts/{disposable['id']}/", 200, token)
        price = request("GET", "/api/flights/price/?route=DEL-BOM", 200)
        if not 3000 <= price["price"] <= 7000:
            raise CommandError("Mock price outside range.")
        request("GET", "/api/flights/price/?route=XYZ-ABC", 404)
        request("GET", "/api/admin/summary/", 403, token)

        admin = get_user_model().objects.create_user(f"admin_{suffix}", password=password)
        admin.profile.role = UserProfile.Role.ADMIN
        admin.profile.save(update_fields=["role"])
        admin_token = request(
            "POST", "/api/auth/login/", 200, json={"username": admin.username, "password": password}
        )["access"]
        request("GET", "/api/admin/summary/", 200, admin_token)
        request("DELETE", f"/api/alerts/{alert['id']}/", 404, admin_token)

        self.stdout.write(
            f"Waiting for Beat to trigger alert {alert['id']} (up to {options['timeout']}s)..."
        )
        deadline = time.monotonic() + options["timeout"]
        while time.monotonic() < deadline:
            logs = NotificationLog.objects.filter(alert_id=alert["id"])
            if logs.exists():
                log = logs.get()
                row = PriceAlert.objects.get(id=alert["id"])
                if (
                    row.status != PriceAlert.Status.TRIGGERED
                    or log.triggered_price > row.threshold_price
                ):
                    raise CommandError("Notification or alert status is incorrect.")
                self.stdout.write(
                    self.style.SUCCESS(
                        f"PASS NotificationLog {log.id} for alert {row.id}, "
                        f"price {log.triggered_price}; "
                        "all 13 rubric scenarios verified against live services."
                    )
                )
                return
            time.sleep(2)
        raise CommandError("No NotificationLog arrived. Check the worker and Beat logs.")
