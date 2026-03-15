from decimal import Decimal, ROUND_HALF_UP

from rest_framework import serializers
from django.conf import settings

from .models import Wallet, WalletTransaction


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
