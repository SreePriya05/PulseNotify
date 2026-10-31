import random

from django.contrib.auth import authenticate
from django.db import IntegrityError, transaction
from django.db.models import CharField, Count, Q, Value
from django.db.models.functions import Concat
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken

from .models import NotificationLog, PriceAlert
from .permissions import IsAdminUser
from .prices import MOCK_PRICES
from .serializers import LoginSerializer, PriceAlertSerializer, RegisterSerializer


def token_response(user):
    return {
        "username": user.username,
        "access": str(AccessToken.for_user(user)),
        "role": user.profile.role,
    }


class RegisterView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                user = serializer.save()
        except IntegrityError:
            return Response({"username": ["Username already exists."]}, status=400)
        return Response(token_response(user), status=status.HTTP_201_CREATED)


class LoginView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(request=request, **serializer.validated_data)
        if user is None:
            return Response({"detail": "Invalid credentials."}, status=401)
        return Response(token_response(user))


class AlertListCreateView(generics.ListCreateAPIView):
    serializer_class = PriceAlertSerializer

    def get_queryset(self):
        return PriceAlert.objects.filter(user=self.request.user).order_by("-created_at", "-id")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class AlertDeactivateView(APIView):
    def delete(self, request, id):
        # The same row lock is used by the notification task to serialize state changes.
        with transaction.atomic():
            try:
                alert = get_object_or_404(PriceAlert.objects.select_for_update(), id=id)
            except Http404:
                raise Http404("Not found.") from None
            if alert.user != request.user:
                raise Http404
            alert.status = PriceAlert.Status.INACTIVE
            alert.save(update_fields=["status"])
        return Response({"status": "inactive"})


class FlightPriceView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        route = request.query_params.get("route", "")
        price_range = MOCK_PRICES.get(route)
        if not price_range:
            return Response({"error": "Route not found"}, status=404)
        return Response({"route": route, "price": random.randint(*price_range)})


class AdminSummaryView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        counts = PriceAlert.objects.aggregate(
            total_alerts=Count("id"),
            active_alerts=Count("id", filter=Q(status=PriceAlert.Status.ACTIVE)),
            triggered_alerts=Count("id", filter=Q(status=PriceAlert.Status.TRIGGERED)),
        )
        counts.update(NotificationLog.objects.aggregate(total_notifications=Count("id")))
        counts["top_routes"] = list(
            PriceAlert.objects.values("origin", "destination")
            .annotate(
                alert_count=Count("id"),
                route=Concat("origin", Value("-"), "destination", output_field=CharField()),
            )
            .order_by("-alert_count", "route")
            .values("route", "alert_count")[:5]
        )
        return Response(counts)
