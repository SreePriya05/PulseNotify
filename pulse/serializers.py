from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from rest_framework import serializers

from .models import PriceAlert
from .prices import MOCK_PRICES

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    class Meta:
        model = User
        fields = ["username", "password", "email"]
        extra_kwargs = {"email": {"required": True, "allow_blank": False}}

    def validate(self, attrs):
        try:
            validate_password(
                attrs["password"], User(username=attrs["username"], email=attrs["email"])
            )
        except ValidationError as exc:
            raise serializers.ValidationError({"password": exc.messages}) from exc
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class PriceAlertSerializer(serializers.ModelSerializer):
    threshold_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0.01")
    )

    class Meta:
        model = PriceAlert
        fields = ["id", "origin", "destination", "threshold_price", "status", "created_at"]
        read_only_fields = ["id", "status", "created_at"]

    def validate(self, attrs):
        origin = attrs["origin"].strip().upper()
        destination = attrs["destination"].strip().upper()
        if f"{origin}-{destination}" not in MOCK_PRICES:
            raise serializers.ValidationError(
                "Unsupported route. Choose: " + ", ".join(MOCK_PRICES)
            )
        attrs.update(origin=origin, destination=destination)
        return attrs
