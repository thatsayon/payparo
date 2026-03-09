from django.db import models
from django.conf import settings

from cloudinary.models import CloudinaryField
from app.common.models import BaseModel


class Escrow(BaseModel):

    class Role(models.TextChoices):
        SELLER = "seller", "As Seller"
        BUYER  = "buyer",  "As Buyer"

    class ItemType(models.TextChoices):
        PRODUCT = "product", "Product"
        SERVICE = "service", "Service"

    class Status(models.TextChoices):
        PENDING   = "pending",   "Pending"
        ACTIVE    = "active",    "Active"
        COMPLETED = "completed", "Completed"
        DISPUTED  = "disputed",  "Disputed"
        CANCELLED = "cancelled", "Cancelled"

    # The user who creates this escrow
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_escrows",
    )

    # The other party (seller or buyer on the other side)
    receiver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_escrows",
    )

    # The role the creator is taking in this escrow
    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.SELLER,
    )

    item_type = models.CharField(
        max_length=10,
        choices=ItemType.choices,
        default=ItemType.PRODUCT,
    )

    product_name = models.CharField(max_length=255)
    description  = models.TextField(blank=True)

    price    = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default="USD")

    status = models.CharField(
        max_length=15,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_by", "status"]),
            models.Index(fields=["receiver", "status"]),
        ]

    def __str__(self):
        return f"Escrow #{self.id} — {self.product_name} ({self.status})"


class EscrowImage(models.Model):
    """One Cloudinary image attached to an Escrow (multiple allowed)."""

    escrow = models.ForeignKey(
        Escrow,
        on_delete=models.CASCADE,
        related_name="images",
    )
    image      = CloudinaryField()
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]

    def __str__(self):
        return f"Image for Escrow {self.escrow_id}"
