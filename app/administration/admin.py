from django.contrib import admin
from .models import FeeConfiguration


@admin.register(FeeConfiguration)
class FeeConfigurationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "withdraw_fee",
        "withdraw_fee_percentage",
        "withdraw_min_amount",
        "stripe_fee_percentage",
        "stripe_fixed_fee",
        "escrow_fee",
    )
