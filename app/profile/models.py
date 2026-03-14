from django.db import models
from django.conf import settings

from app.common.models import BaseModel


class Wallet(BaseModel):
    """
    One wallet per user. Stores the current balance.
    Auto-created on first access via the API.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wallet",
    )

    balance = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0.00,
    )

    currency = models.CharField(max_length=10, default="USD")

    class Meta:
        indexes = [
            models.Index(fields=["user"]),
        ]

    def __str__(self):
        return f"Wallet ({self.user.email}) — {self.currency} {self.balance}"


class WalletTransaction(BaseModel):
    """
    Immutable log of every wallet event (deposit, withdrawal, etc.).
    """

    class TransactionType(models.TextChoices):
        DEPOSIT = "deposit", "Deposit"
        WITHDRAWAL = "withdrawal", "Withdrawal"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.CASCADE,
        related_name="transactions",
    )

    transaction_type = models.CharField(
        max_length=15,
        choices=TransactionType.choices,
    )

    amount = models.DecimalField(max_digits=14, decimal_places=2)
    fee = models.DecimalField(max_digits=14, decimal_places=2, default=0.00)
    total_charged = models.DecimalField(max_digits=14, decimal_places=2, default=0.00)

    stripe_payment_intent_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        unique=True,
        db_index=True,
    )

    status = models.CharField(
        max_length=15,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    description = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["wallet", "status"]),
            models.Index(fields=["stripe_payment_intent_id"]),
        ]

    def __str__(self):
        return (
            f"{self.get_transaction_type_display()} — "
            f"{self.amount} ({self.get_status_display()})"
        )
