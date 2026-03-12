from django.db import models
from app.common.models import BaseModel

class FeeConfiguration(BaseModel):
    escrow_fee = models.DecimalField(max_digits=6, decimal_places=2)
