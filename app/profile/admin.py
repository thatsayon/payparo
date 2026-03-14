from django.contrib import admin

from .models import Wallet, WalletTransaction


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "balance", "currency", "created_at")
    search_fields = ("user__email", "user__username")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "wallet",
        "transaction_type",
        "amount",
        "fee",
        "total_charged",
        "status",
        "created_at",
    )
    list_filter = ("transaction_type", "status")
    search_fields = ("wallet__user__email", "stripe_payment_intent_id")
    readonly_fields = ("id", "created_at", "updated_at")
