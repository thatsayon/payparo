import logging
from decimal import Decimal, ROUND_HALF_UP

import stripe
from django.conf import settings
from django.db import transaction as db_transaction
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from rest_framework import permissions, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Wallet, WalletTransaction
from .serializers import (
    AddBalanceSerializer,
    WalletSerializer,
    WalletTransactionSerializer,
)

logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _get_or_create_wallet(user) -> Wallet:
    """Return the user's wallet, creating one if it doesn't exist."""
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


# ──────────────────────────────────────────────
# Wallet Balance
# ──────────────────────────────────────────────

class WalletBalanceView(APIView):
    """
    GET — Return the authenticated user's wallet balance.
    Auto-creates a wallet on first access.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        wallet = _get_or_create_wallet(request.user)
        serializer = WalletSerializer(wallet)
        return Response(
            {"success": True, "wallet": serializer.data},
            status=status.HTTP_200_OK,
        )


# ──────────────────────────────────────────────
# Add Balance (Create Stripe PaymentIntent)
# ──────────────────────────────────────────────

class CreatePaymentIntentView(APIView):
    """
    POST — Accept an amount, calculate a 3% Stripe fee,
    create a Stripe PaymentIntent, and return the client_secret.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = AddBalanceSerializer(data=request.data)
        if not serializer.is_valid():
            errors = serializer.errors
            first_field = next(iter(errors))
            first_msg = (
                errors[first_field][0]
                if isinstance(errors[first_field], list)
                else str(errors[first_field])
            )
            if first_field != "non_field_errors":
                first_msg = f"{first_field}: {first_msg}"
            return Response(
                {"error": first_msg},
                status=status.HTTP_400_BAD_REQUEST,
            )

        amount = serializer.validated_data["amount"]
        fee_info = serializer.get_fee_breakdown(amount)

        total_charge = Decimal(fee_info["total_charge"])
        fee = Decimal(fee_info["fee"])

        # Stripe expects amount in the smallest currency unit (cents for USD)
        stripe_amount = int(
            (total_charge * Decimal("100")).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )

        try:
            intent = stripe.PaymentIntent.create(
                amount=stripe_amount,
                currency="usd",
                metadata={
                    "user_id": str(request.user.id),
                    "wallet_amount": str(amount),
                    "fee": str(fee),
                },
            )
        except stripe.error.StripeError as e:
            logger.error("Stripe error creating PaymentIntent: %s", e)
            return Response(
                {"error": "Payment service unavailable. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Create a pending transaction record
        wallet = _get_or_create_wallet(request.user)
        WalletTransaction.objects.create(
            wallet=wallet,
            transaction_type=WalletTransaction.TransactionType.DEPOSIT,
            amount=amount,
            fee=fee,
            total_charged=total_charge,
            stripe_payment_intent_id=intent.id,
            status=WalletTransaction.Status.PENDING,
            description="Wallet top-up via Stripe",
        )

        return Response(
            {
                "success": True,
                "client_secret": intent.client_secret,
                "payment_intent_id": intent.id,
                "publishable_key": settings.STRIPE_PUBLISHABLE_KEY,
                "wallet_amount": fee_info["wallet_amount"],
                "fee": fee_info["fee"],
                "fee_percent": fee_info["fee_percent"],
                "total_charge": fee_info["total_charge"],
            },
            status=status.HTTP_200_OK,
        )


# ──────────────────────────────────────────────
# Stripe Webhook
# ──────────────────────────────────────────────

@method_decorator(csrf_exempt, name="dispatch")
class StripeWebhookView(APIView):
    """
    POST — Receive Stripe webhook events.
    Verifies the webhook signature and processes payment_intent events.
    No authentication required (verified by Stripe signature instead).
    """
    permission_classes = [permissions.AllowAny]
    parser_classes = [JSONParser]

    def post(self, request):
        payload = request.body
        sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
        webhook_secret = settings.STRIPE_WEBHOOK_SECRET

        # If webhook secret is configured, verify signature
        if webhook_secret:
            try:
                event = stripe.Webhook.construct_event(
                    payload, sig_header, webhook_secret
                )
            except ValueError:
                logger.warning("Stripe webhook: invalid payload")
                return Response(
                    {"error": "Invalid payload"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            except stripe.error.SignatureVerificationError:
                logger.warning("Stripe webhook: invalid signature")
                return Response(
                    {"error": "Invalid signature"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            # No secret configured — parse raw JSON (dev/testing only)
            import json
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                return Response(
                    {"error": "Invalid JSON"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        event_type = event.get("type") if isinstance(event, dict) else event.type
        data_object = (
            event.get("data", {}).get("object", {})
            if isinstance(event, dict)
            else event.data.object
        )

        if event_type == "payment_intent.succeeded":
            self._handle_success(data_object)
        elif event_type == "payment_intent.payment_failed":
            self._handle_failure(data_object)

        return Response({"received": True}, status=status.HTTP_200_OK)

    @staticmethod
    def _handle_success(payment_intent):
        pi_id = (
            payment_intent.get("id")
            if isinstance(payment_intent, dict)
            else payment_intent.id
        )
        try:
            txn = WalletTransaction.objects.select_related("wallet").get(
                stripe_payment_intent_id=pi_id
            )
        except WalletTransaction.DoesNotExist:
            logger.warning("Webhook: no transaction for PI %s", pi_id)
            return

        if txn.status == WalletTransaction.Status.COMPLETED:
            return  # idempotent

        with db_transaction.atomic():
            txn.status = WalletTransaction.Status.COMPLETED
            txn.save(update_fields=["status", "updated_at"])

            wallet = txn.wallet
            wallet.balance += txn.amount
            wallet.save(update_fields=["balance", "updated_at"])

        logger.info(
            "Wallet %s credited %s (PI: %s)", wallet.id, txn.amount, pi_id
        )

    @staticmethod
    def _handle_failure(payment_intent):
        pi_id = (
            payment_intent.get("id")
            if isinstance(payment_intent, dict)
            else payment_intent.id
        )
        try:
            txn = WalletTransaction.objects.get(
                stripe_payment_intent_id=pi_id
            )
        except WalletTransaction.DoesNotExist:
            return

        if txn.status != WalletTransaction.Status.PENDING:
            return

        txn.status = WalletTransaction.Status.FAILED
        txn.save(update_fields=["status", "updated_at"])
        logger.info("Transaction %s marked FAILED (PI: %s)", txn.id, pi_id)


# ──────────────────────────────────────────────
# Transaction History
# ──────────────────────────────────────────────

class TransactionHistoryView(APIView):
    """
    GET — Paginated list of the authenticated user's wallet transactions.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        wallet = _get_or_create_wallet(request.user)
        transactions = WalletTransaction.objects.filter(wallet=wallet)

        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(transactions, request, view=self)

        serializer = WalletTransactionSerializer(page, many=True)
        return Response(
            {
                "success": True,
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": serializer.data,
            },
            status=status.HTTP_200_OK,
        )
