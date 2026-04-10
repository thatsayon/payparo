from decimal import Decimal, ROUND_HALF_UP

from rest_framework import serializers
from django.conf import settings

from django.contrib.auth import get_user_model
from django.db.models import Q, Avg

from app.excrow.models import Escrow
from .models import Wallet, WalletTransaction, BankAccount, PaypalAccount

User = get_user_model()

class WalletSerializer(serializers.ModelSerializer):
    """Read-only representation of a user's wallet."""

    class Meta:
        model = Wallet
        fields = ("id", "balance", "currency")
        read_only_fields = fields


class AddBalanceSerializer(serializers.Serializer):
    """
    Input: amount to add to the wallet.
    Output: fee breakdown and total charge.
    """

    amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("1.00"),
    )

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Amount must be greater than zero.")
        return value

    def get_fee_breakdown(self, amount: Decimal) -> dict:
        from app.administration.models import FeeConfiguration

        config = FeeConfiguration.objects.first()
        if config:
            fee_percent = config.stripe_fee_percentage
            fixed_fee = config.stripe_fixed_fee
        else:
            # Fallback to defaults or settings if configuration doesn't exist
            fee_percent = Decimal(str(getattr(settings, "STRIPE_FEE_PERCENT", "3.00")))
            fixed_fee = Decimal("0.00")

        # Formula: (Amount * Percentage / 100) + Fixed Fee
        percentage_fee = (amount * fee_percent / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        total_fee = percentage_fee + fixed_fee
        total_charge = amount + total_fee
        
        return {
            "wallet_amount": str(amount),
            "fee": str(total_fee),
            "fee_percent": str(fee_percent),
            "fixed_fee": str(fixed_fee),
            "total_charge": str(total_charge),
        }


class WalletTransactionSerializer(serializers.ModelSerializer):
    """Read-only listing of wallet transactions."""

    transaction_type_display = serializers.CharField(
        source="get_transaction_type_display", read_only=True
    )
    status_display = serializers.CharField(
        source="get_status_display", read_only=True
    )

    class Meta:
        model = WalletTransaction
        fields = (
            "id",
            "transaction_type",
            "transaction_type_display",
            "amount",
            "fee",
            "total_charged",
            "stripe_payment_intent_id",
            "status",
            "status_display",
            "description",
            "created_at",
        )
        read_only_fields = fields


class ProfileHomeSerializer(serializers.ModelSerializer):
    kyc_status = serializers.CharField(read_only=True)
    total_completed_escrows = serializers.SerializerMethodField()
    rating = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            'id',
            'email',
            'full_name',
            'profile_pic',
            'kyc_status',
            'total_completed_escrows',
            'rating'
        )

    def get_total_completed_escrows(self, obj):
        return Escrow.objects.filter(
            Q(created_by=obj) | Q(receiver=obj),
            status=Escrow.Status.COMPLETED
        ).count()

    def get_rating(self, obj):
        avg = obj.received_reviews.aggregate(average=Avg('rating'))['average']
        return round(avg, 2) if avg else 0.0


class BankAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankAccount
        fields = (
            "id",
            "bank_name",
            "account_holder_name",
            "account_number",
            "routing_number",
            "created_at",
            "updated_at"
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def create(self, validated_data):
        return BankAccount.objects.create(user=self.context["request"].user, **validated_data)


class PaypalAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaypalAccount
        fields = (
            "id",
            "paypal_email",
            "full_name",
            "created_at",
            "updated_at"
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def create(self, validated_data):
        return PaypalAccount.objects.create(user=self.context["request"].user, **validated_data)


class PhoneNumberSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("phone_number",)

