from django.contrib import admin

from .models import NotificationLog, PriceAlert, UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "role", "created_at"]
    list_filter = ["role"]


@admin.register(PriceAlert)
class PriceAlertAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "origin", "destination", "threshold_price", "status"]
    list_filter = ["status", "origin", "destination"]


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ["id", "alert", "triggered_price", "notified_at"]
    readonly_fields = ["alert", "triggered_price", "message", "notified_at"]

    def has_add_permission(self, request):
        return False
