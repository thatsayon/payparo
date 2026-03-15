from django.urls import path

from .views import (
    WalletBalanceView,
    StripeFeeConfigView,
    CreatePaymentIntentView,
    StripeWebhookView,
    TransactionHistoryView,
)

urlpatterns = [
    # Wallet
    path("wallet/balance/", WalletBalanceView.as_view(), name="wallet-balance"),
    path("wallet/stripe-fee/", StripeFeeConfigView.as_view(), name="stripe-fee-config"),
    path("wallet/add-balance/", CreatePaymentIntentView.as_view(), name="wallet-add-balance"),
    path("wallet/transactions/", TransactionHistoryView.as_view(), name="wallet-transactions"),

    # Stripe webhook (no auth — verified by signature)
    path("wallet/webhook/stripe/", StripeWebhookView.as_view(), name="stripe-webhook"),
]
