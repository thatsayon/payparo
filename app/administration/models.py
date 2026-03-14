from django.db import models
from app.common.models import BaseModel

class FeeConfiguration(BaseModel):
    escrow_fee = models.DecimalField(max_digits=6, decimal_places=2)
    stripe_fee_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    stripe_fixed_fee = models.DecimalField(max_digits=6, decimal_places=2)

