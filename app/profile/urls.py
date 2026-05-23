from django.urls import path

from .views import (
    WalletBalanceView,
    WithdrawPageView,
    WithdrawRequestView,
    StripeFeeConfigView,
    CreatePaymentIntentView,
    StripeWebhookView,
    TransactionHistoryView,
    ProfileHome,
    BankAccountView,
    PaypalAccountView,
    UpdatePhoneNumberView,
    WithdrawFeeConfigView,
    UpdateProfileView,
    PaypalWithdrawHistoryView,
    BankWithdrawHistoryView,
    CreateSubscriptionSessionView,
    UserSubscriptionStatusView,
)

urlpatterns = [
    # Wallet
    path("wallet/balance/", WalletBalanceView.as_view(), name="wallet-balance"),
    path("wallet/withdraw-page/", WithdrawPageView.as_view(), name="wallet-withdraw-page"),
    path("wallet/withdraw-request/", WithdrawRequestView.as_view(), name="wallet-withdraw-request"),
    path("wallet/stripe-fee/", StripeFeeConfigView.as_view(), name="stripe-fee-config"),
    path("wallet/add-balance/", CreatePaymentIntentView.as_view(), name="wallet-add-balance"),
    path("wallet/transactions/", TransactionHistoryView.as_view(), name="wallet-transactions"),
    path("wallet/subscription/session/", CreateSubscriptionSessionView.as_view(), name="wallet-subscription-session"),
    path("wallet/subscription/status/", UserSubscriptionStatusView.as_view(), name="wallet-subscription-status"),

    # Stripe webhook (no auth — verified by signature)
    path("wallet/webhook/stripe/", StripeWebhookView.as_view(), name="stripe-webhook"),

    # Profile Home
    path("home/", ProfileHome.as_view(), name="profile-home"),

    # Bank Accounts (Singleton)
    path("banks/", BankAccountView.as_view(), name="bank-account"),

    # PayPal Accounts (Singleton)
    path("paypal/", PaypalAccountView.as_view(), name="paypal-account"),

    # Phone Number
    path("phone/", UpdatePhoneNumberView.as_view(), name="update-phone-number"),

    # Withdraw
    path("withdraw/fee/", WithdrawFeeConfigView.as_view(), name="withdraw-fee"),

    # Update Profile
    path("update/", UpdateProfileView.as_view(), name="update-profile"),

    # Withdraw History
    path("withdraw/history/paypal/", PaypalWithdrawHistoryView.as_view(), name="paypal-withdraw-history"),
    path("withdraw/history/bank/", BankWithdrawHistoryView.as_view(), name="bank-withdraw-history"),
]
