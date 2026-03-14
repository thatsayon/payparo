from django.db import models
from django.conf import settings
import string
import random

from cloudinary.models import CloudinaryField
from app.common.models import BaseModel

def generate_order_id():
    """Generates a random 11-character alphanumeric string like '152JkRLPR03'."""
    characters = string.ascii_letters + string.digits
    return "".join(random.choices(characters, k=11))


class Escrow(BaseModel):

    class Role(models.TextChoices):
        SELLER = "seller", "As Seller"
        BUYER  = "buyer",  "As Buyer"

    class ItemType(models.TextChoices):
        PRODUCT = "product", "Product"
        SERVICE = "service", "Service"

    class PaymentOption(models.TextChoices):
        SINGLE      = "single",      "Single Payment"
        INSTALLMENT = "installment", "Custom Installments"

    class Status(models.TextChoices):
        CREATED            = "created",            "Created"
        FUNDED             = "funded",             "Funded"
        ACCEPTED           = "accepted",           "Accepted"
        IN_PROGRESS        = "in_progress",        "In Progress"
        SHIPPED            = "shipped",            "Shipped"
        DELIVERED          = "delivered",          "Delivered"
        UNDER_REVIEW       = "under_review",       "Under Review"
        ISSUE_RAISED       = "issue_raised",       "Issue Raised"
        RETURN_IN_PROGRESS = "return_in_progress", "Return In Progress"
        RESOLVED           = "resolved",           "Resolved"
        REFUNDED           = "refunded",           "Refunded"
        PAYMENT_RELEASED   = "payment_released",   "Payment Released"
        COMPLETED          = "completed",          "Completed"
        CANCELLED          = "cancelled",          "Cancelled"

    # The user who creates this escrow
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_escrows",
    )

    # The other party (receiver)
    receiver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_escrows",
    )

    # Unique identifier for the order
    order_id = models.CharField(
        max_length=20,
        unique=True,
        editable=False,
        default=generate_order_id,
        db_index=True,
    )

    # Role the creator takes
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
    description  = models.TextField()

    payment_option = models.CharField(
        max_length=15,
        choices=PaymentOption.choices,
        default=PaymentOption.SINGLE,
    )

    # Used when payment_option == SINGLE; null for installment plans
    price    = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    fee_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=10, default="USD")

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.CREATED,
        db_index=True,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_by", "status"]),
            models.Index(fields=["receiver", "status"]),
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_status = self.status

    def __str__(self):
        return f"Escrow #{self.id} — {self.product_name} ({self.status})"

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        status_changed = not is_new and self.status != self._original_status

        super().save(*args, **kwargs)

        # Log status history if new or status changed
        if is_new or status_changed:
            EscrowStatusHistory.objects.create(escrow=self, status=self.status)
        self._original_status = self.status


class EscrowStatusHistory(models.Model):
    """Tracks the timeline of an Escrow's status changes."""
    escrow = models.ForeignKey(Escrow, on_delete=models.CASCADE, related_name="status_history")
    status = models.CharField(max_length=20, choices=Escrow.Status.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Escrow {self.escrow.id} changed to {self.status} at {self.created_at}"


class EscrowInstallment(models.Model):
    """
    A single installment amount for a Custom Installments escrow.
    Ordered by 'order' field (1-based).
    """
    escrow = models.ForeignKey(
        Escrow,
        on_delete=models.CASCADE,
        related_name="installments",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    order  = models.PositiveSmallIntegerField(default=1)

    # Payment Tracking
    is_paid = models.BooleanField(default=False, db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    payment_intent_id = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        status = "Paid" if self.is_paid else "Unpaid"
        return f"Installment #{self.order} — {self.amount} for Escrow {self.escrow_id} ({status})"


class EscrowImage(BaseModel):
    """Product images for an Escrow (multiple allowed, minimum 3 required at creation)."""

    escrow = models.ForeignKey(
        Escrow,
        on_delete=models.CASCADE,
        related_name="images",
    )
    image       = CloudinaryField()
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]

    def __str__(self):
        return f"Image for Escrow {self.escrow_id}"


class EscrowDocument(BaseModel):
    """
    Optional supporting documents for an Escrow.
    Accepted formats: PDF, DOC, DOCX, JPG, PNG — stored in Cloudinary.
    """
    escrow = models.ForeignKey(
        Escrow,
        on_delete=models.CASCADE,
        related_name="documents",
    )
    file        = CloudinaryField(resource_type="auto")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]

    def __str__(self):
        return f"Document for Escrow {self.escrow_id}"
