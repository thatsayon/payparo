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

