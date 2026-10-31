import logging
from decimal import Decimal, InvalidOperation

import requests
from celery import shared_task
from django.conf import settings
from django.db import transaction

from .models import NotificationLog, PriceAlert

logger = logging.getLogger(__name__)


def parse_price(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("Price must be a positive finite amount.")
    try:
        price = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid price.") from exc
    if not price.is_finite() or price <= 0 or price > Decimal("99999999.99"):
        raise ValueError("Price outside supported range.")
    return price


@shared_task
def check_prices():
    active_alerts = PriceAlert.objects.filter(status=PriceAlert.Status.ACTIVE)
    routes = list(active_alerts.values_list("origin", "destination").distinct())
    queued = 0
    for origin, destination in routes:
        route = f"{origin}-{destination}"
        try:
            response = requests.get(
                settings.FLIGHT_PRICE_URL,
                params={"route": route},
                timeout=settings.FLIGHT_PRICE_TIMEOUT,
            )
            if response.status_code != 200:
                logger.warning("Price feed returned %s for %s", response.status_code, route)
                continue
            data = response.json()
            if not isinstance(data, dict) or data.get("route") != route:
                raise ValueError("Unexpected route in price response.")
            current_price = parse_price(data.get("price"))
        except (requests.RequestException, ValueError, TypeError):
            logger.exception("Unable to fetch a valid price for %s", route)
            continue

        for alert in active_alerts.filter(origin=origin, destination=destination):
            if current_price <= alert.threshold_price:
                send_notification.delay(alert.id, str(current_price))
                queued += 1
    return {"routes_checked": len(routes), "notifications_queued": queued}


@shared_task
def send_notification(alert_id, triggered_price):
    price = parse_price(triggered_price)
    with transaction.atomic():
        alert = PriceAlert.objects.select_for_update().filter(id=alert_id).first()
        if alert is None or alert.status != PriceAlert.Status.ACTIVE:
            return {"notified": False}
        if price > alert.threshold_price:
            return {"notified": False}
        message = (
            f"Price alert triggered! {alert.origin}-{alert.destination} "
            f"is now ₹{price} - at or below your threshold of ₹{alert.threshold_price}"
        )
        log = NotificationLog.objects.create(alert=alert, triggered_price=price, message=message)
        alert.status = PriceAlert.Status.TRIGGERED
        alert.save(update_fields=["status"])
    logger.info("Notification %s written for alert %s", log.id, alert_id)
    return {"notified": True, "notification_id": log.id}
