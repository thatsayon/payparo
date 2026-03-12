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

    class PaymentOption(models.TextChoices):
        SINGLE      = "single",      "Single Payment"
        INSTALLMENT = "installment", "Custom Installments"

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

    # The other party (receiver)
    receiver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_escrows",
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

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"Installment #{self.order} — {self.amount} for Escrow {self.escrow_id}"


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
