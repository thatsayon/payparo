from django.db import models
from app.common.models import BaseModel


class FeeConfiguration(BaseModel):
    escrow_fee = models.DecimalField(max_digits=6, decimal_places=2)
    stripe_fee_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    stripe_fixed_fee = models.DecimalField(max_digits=6, decimal_places=2)
    withdraw_fee = models.DecimalField(max_digits=6, decimal_places=2)
    withdraw_fee_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    withdraw_min_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=10.00,
        help_text="Minimum amount a user can request to withdraw.",
    )

    def __str__(self):
        return "Fee Configuration"

    class Meta:
        verbose_name = "Fee Configuration"
        verbose_name_plural = "Fee Configurations"


class MarketingBanner(BaseModel):
    from cloudinary.models import CloudinaryField
    title = models.CharField(max_length=255, blank=True, null=True, help_text="Optional banner title")
    image = CloudinaryField(resource_type="image", help_text="Cloudinary image upload for marketing banner")
    link = models.URLField(max_length=500, blank=True, null=True, help_text="Link to open when banner is clicked")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Marketing Banner"
        verbose_name_plural = "Marketing Banners"

    def __str__(self):
        return self.title or f"Banner {self.id}"


