import string
import random
from django.db import models
from django.conf import settings
from app.common.models import BaseModel

def generate_referral_code():
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=8))

class ReferralProfile(BaseModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name="referral_profile"
    )
    referral_code = models.CharField(max_length=50, unique=True, default=generate_referral_code)
    
    # The person who invited this user
    referred_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name="referred_users"
    )
    referred_at = models.DateTimeField(null=True, blank=True)
    
    total_earnings = models.DecimalField(max_digits=14, decimal_places=2, default=0.00)

    REFERRAL_COMMISSION_AMOUNT = 10.00

    def __str__(self):
        return f"Referral Profile: {self.user.email} ({self.referral_code})"

class ReferralEarning(BaseModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"

    referrer = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name="referral_earnings"
    )
    
    referred_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name="contributed_earnings"
    )
    
    escrow = models.ForeignKey(
        'excrow.Escrow', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True
    )
    
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    def __str__(self):
        return f"Earning for {self.referrer.email} from {self.referred_user.email} - ${self.amount}"
