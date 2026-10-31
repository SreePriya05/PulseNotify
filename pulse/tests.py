from decimal import Decimal
from unittest.mock import Mock, patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from .models import NotificationLog, PriceAlert, UserProfile
from .tasks import check_prices, send_notification

User = get_user_model()


class AlertTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="SecurePass!925")
        self.other = User.objects.create_user("bob", password="SecurePass!925")
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.user)}")

    def alert(self, **kwargs):
        defaults = {
            "user": self.user,
            "origin": "DEL",
            "destination": "BOM",
            "threshold_price": Decimal("4500.00"),
        }
        defaults.update(kwargs)
        return PriceAlert.objects.create(**defaults)


@override_settings(
    FLIGHT_PRICE_URL="http://localhost:8000/api/flights/price/", FLIGHT_PRICE_TIMEOUT=5.0
)
class PriceThresholdTest(AlertTestCase):
    @patch("pulse.tasks.send_notification.delay")
    @patch("pulse.tasks.requests.get")
    def test_below_equal_and_above_threshold(self, get, delay):
        alert = self.alert()
        for price, should_trigger in [(4200, True), (5000, False), (4500, True)]:
            with self.subTest(price=price):
                get.return_value = Mock(status_code=200)
                get.return_value.json.return_value = {"route": "DEL-BOM", "price": price}
                delay.reset_mock()
                check_prices()
                if should_trigger:
                    delay.assert_called_once_with(alert.id, str(price))
                else:
                    delay.assert_not_called()
        get.assert_called_with(
            "http://localhost:8000/api/flights/price/", params={"route": "DEL-BOM"}, timeout=5.0
        )

    @patch("pulse.tasks.send_notification.delay")
    @patch("pulse.tasks.requests.get")
    def test_fetches_once_per_active_route_and_skips_inactive(self, get, delay):
        self.alert()
        self.alert(user=self.other, threshold_price=4600)
        self.alert(origin="BLR", destination="HYD", status=PriceAlert.Status.INACTIVE)
        get.return_value = Mock(status_code=200)
        get.return_value.json.return_value = {"route": "DEL-BOM", "price": 4000}
        result = check_prices()
        self.assertEqual(get.call_count, 1)
        self.assertEqual(delay.call_count, 2)
        self.assertEqual(result["notifications_queued"], 2)

    @patch("pulse.tasks.send_notification.delay")
    @patch("pulse.tasks.requests.get")
    def test_feed_failure_does_not_prevent_next_route(self, get, delay):
        self.alert()
        second = self.alert(origin="BLR", destination="HYD")
        response = Mock(status_code=200)
        response.json.return_value = {"route": "BLR-HYD", "price": 2000}

        def feed_response(*args, **kwargs):
            if kwargs["params"]["route"] == "DEL-BOM":
                raise requests.Timeout("feed unavailable")
            return response

        get.side_effect = feed_response
        with self.assertLogs("pulse.tasks", level="ERROR"):
            check_prices()
        delay.assert_called_once_with(second.id, "2000")

    @patch("pulse.tasks.send_notification.delay")
    @patch("pulse.tasks.requests.get")
    def test_invalid_feed_prices_are_skipped(self, get, delay):
        self.alert()
        for value in [None, True, -1, "NaN", "Infinity", "not-a-price"]:
            with self.subTest(value=value):
                get.return_value = Mock(status_code=200)
                get.return_value.json.return_value = {"route": "DEL-BOM", "price": value}
                with self.assertLogs("pulse.tasks", level="ERROR"):
                    check_prices()
        delay.assert_not_called()

    @patch("pulse.tasks.send_notification.delay")
    @patch("pulse.tasks.requests.get")
    def test_non_success_and_mismatched_route_are_skipped(self, get, delay):
        self.alert()
        get.return_value = Mock(status_code=404)
        with self.assertLogs("pulse.tasks", level="WARNING"):
            check_prices()
        get.return_value.status_code = 200
        get.return_value.json.return_value = {"route": "WRONG-ROUTE", "price": 1}
        with self.assertLogs("pulse.tasks", level="ERROR"):
            check_prices()
        delay.assert_not_called()


class NotificationLogTest(AlertTestCase):
    def test_notification_task_writes_message_price_and_status(self):
        alert = self.alert()
        send_notification(alert.id, "4200")
        log = NotificationLog.objects.get(alert=alert)
        self.assertEqual(log.triggered_price, Decimal("4200"))
        self.assertIn("DEL-BOM", log.message)
        self.assertIn("4200", log.message)
        self.assertIn("4500", log.message)
        alert.refresh_from_db()
        self.assertEqual(alert.status, PriceAlert.Status.TRIGGERED)

    def test_duplicate_deliveries_create_one_notification(self):
        alert = self.alert()
        send_notification(alert.id, "4200")
        send_notification(alert.id, "4200")
        self.assertEqual(NotificationLog.objects.filter(alert=alert).count(), 1)

    def test_inactive_triggered_missing_and_above_threshold_are_skipped(self):
        for state in [PriceAlert.Status.INACTIVE, PriceAlert.Status.TRIGGERED]:
            send_notification(self.alert(status=state).id, "4200")
        send_notification(999999, "4200")
        alert = self.alert()
        send_notification(alert.id, "5000")
        self.assertFalse(NotificationLog.objects.exists())
        alert.refresh_from_db()
        self.assertEqual(alert.status, PriceAlert.Status.ACTIVE)

    @patch("pulse.tasks.NotificationLog.objects.create", side_effect=RuntimeError("write failed"))
    def test_log_failure_preserves_active_state(self, create):
        alert = self.alert()
        with self.assertRaises(RuntimeError):
            send_notification(alert.id, "4200")
        alert.refresh_from_db()
        self.assertEqual(alert.status, PriceAlert.Status.ACTIVE)


class AlertScopingTest(AlertTestCase):
    def test_list_only_returns_current_users_alerts(self):
        own = self.alert()
        self.alert(user=self.other, origin="BLR", destination="HYD")
        response = self.client.get("/api/alerts/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()], [own.id])

    def test_empty_list(self):
        self.assertEqual(self.client.get("/api/alerts/").json(), [])

    def test_other_users_alert_and_missing_alert_return_same_404(self):
        other = self.alert(user=self.other)
        response = self.client.delete(f"/api/alerts/{other.id}/")
        missing = self.client.delete("/api/alerts/999999/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(response.json(), missing.json())
        other.refresh_from_db()
        self.assertEqual(other.status, PriceAlert.Status.ACTIVE)

    def test_delete_deactivates_without_removing_row(self):
        alert = self.alert()
        response = self.client.delete(f"/api/alerts/{alert.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "inactive"})
        alert.refresh_from_db()
        self.assertEqual(alert.status, PriceAlert.Status.INACTIVE)

    def test_create_uses_authenticated_user_and_active_status(self):
        response = self.client.post(
            "/api/alerts/",
            {
                "origin": "del",
                "destination": "bom",
                "threshold_price": "4500.00",
                "user": self.other.id,
                "status": "triggered",
            },
        )
        self.assertEqual(response.status_code, 201)
        alert = PriceAlert.objects.get()
        self.assertEqual(alert.user, self.user)
        self.assertEqual(alert.status, PriceAlert.Status.ACTIVE)
        self.assertEqual(alert.origin, "DEL")

    def test_invalid_route_and_threshold_are_rejected(self):
        for payload in [
            {"origin": "XYZ", "destination": "BOM", "threshold_price": "4500"},
            {"origin": "DEL", "destination": "BOM", "threshold_price": "0"},
            {"origin": "DEL", "destination": "BOM", "threshold_price": "-1"},
        ]:
            self.assertEqual(self.client.post("/api/alerts/", payload).status_code, 400)
        self.assertFalse(PriceAlert.objects.exists())

    def test_protected_endpoints_require_jwt(self):
        self.client.credentials()
        self.assertEqual(self.client.get("/api/alerts/").status_code, 401)
        self.assertEqual(self.client.post("/api/alerts/", {}).status_code, 401)
        self.assertEqual(self.client.delete("/api/alerts/1/").status_code, 401)
        self.assertEqual(self.client.get("/api/admin/summary/").status_code, 401)


class AuthenticationTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.payload = {
            "username": "newuser",
            "password": "SecurePass!925",
            "email": "new@example.com",
            "role": "admin",
        }

    def test_registration_creates_profile_with_user_role_and_jwt(self):
        # Even an invalid bearer token must not affect these public endpoints.
        self.client.credentials(HTTP_AUTHORIZATION="Bearer invalid")
        response = self.client.post("/api/auth/register/", self.payload)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["role"], "user")
        user = User.objects.get(username="newuser")
        self.assertEqual(user.profile.role, UserProfile.Role.USER)
        self.assertTrue(user.check_password(self.payload["password"]))
        self.assertEqual(AccessToken(response.json()["access"])["user_id"], str(user.id))

    def test_duplicate_username_returns_400(self):
        self.client.post("/api/auth/register/", self.payload)
        self.assertEqual(self.client.post("/api/auth/register/", self.payload).status_code, 400)

    def test_login_success_wrong_password_and_inactive_user(self):
        user = User.objects.create_user("newuser", password=self.payload["password"])
        response = self.client.post("/api/auth/login/", self.payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.json())
        self.assertEqual(
            self.client.post(
                "/api/auth/login/",
                {
                    "username": "newuser",
                    "password": "wrong",
                },
            ).status_code,
            401,
        )
        user.is_active = False
        user.save()
        self.assertEqual(self.client.post("/api/auth/login/", self.payload).status_code, 401)

    def test_weak_password_is_rejected(self):
        self.payload["password"] = "123"
        self.assertEqual(self.client.post("/api/auth/register/", self.payload).status_code, 400)

    def test_signal_creates_exactly_one_profile(self):
        user = User.objects.create_user("signaluser")
        user.email = "updated@example.com"
        user.save()
        self.assertEqual(UserProfile.objects.filter(user=user).count(), 1)


class PriceFeedTest(TestCase):
    def test_public_feed_returns_integer_in_range(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer invalid")
        response = client.get("/api/flights/price/?route=DEL-BOM")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["route"], "DEL-BOM")
        self.assertIsInstance(response.json()["price"], int)
        self.assertTrue(3000 <= response.json()["price"] <= 7000)

    def test_unknown_and_missing_route_return_404(self):
        client = APIClient()
        for url in ["/api/flights/price/", "/api/flights/price/?route=XYZ-ABC"]:
            self.assertEqual(client.get(url).status_code, 404)


class AdminSummaryTest(AlertTestCase):
    def test_regular_user_is_forbidden_even_if_django_staff(self):
        self.user.is_staff = True
        self.user.save()
        self.assertEqual(self.client.get("/api/admin/summary/").status_code, 403)

    def test_summary_aggregates_all_users_and_routes(self):
        self.user.profile.role = UserProfile.Role.ADMIN
        self.user.profile.save()
        triggered = self.alert()
        send_notification(triggered.id, "4200")
        self.alert(user=self.other)
        self.alert(origin="BLR", destination="HYD", status=PriceAlert.Status.INACTIVE)
        response = self.client.get("/api/admin/summary/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "total_alerts": 3,
                "active_alerts": 1,
                "triggered_alerts": 1,
                "total_notifications": 1,
                "top_routes": [
                    {"route": "DEL-BOM", "alert_count": 2},
                    {"route": "BLR-HYD", "alert_count": 1},
                ],
            },
        )

    def test_empty_summary(self):
        self.user.profile.role = UserProfile.Role.ADMIN
        self.user.profile.save()
        response = self.client.get("/api/admin/summary/")
        self.assertEqual(response.json()["total_alerts"], 0)
        self.assertEqual(response.json()["top_routes"], [])
