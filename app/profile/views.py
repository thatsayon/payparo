import logging
from decimal import Decimal, ROUND_HALF_UP

import stripe
from django.conf import settings
from django.db import transaction as db_transaction
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from rest_framework import permissions, status, generics, mixins, parsers
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import NotFound

from .models import (
    Wallet, 
    WalletTransaction,
    BankAccount,
    PaypalAccount,
    WithdrawTransaction,
)

from .serializers import (
    AddBalanceSerializer,
    WalletSerializer,
    WalletTransactionSerializer,
    ProfileHomeSerializer,
    BankAccountSerializer,
    PaypalAccountSerializer,
    PhoneNumberSerializer,
    ProfileUpdateSerializer,
    PaypalWithdrawHistorySerializer,
    BankWithdrawHistorySerializer,
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
# Withdraw Page (balance + payout accounts)
# ──────────────────────────────────────────────

class WithdrawPageView(APIView):
    """
    GET — Return the authenticated user's wallet balance, withdraw fee info
    from FeeConfiguration, and any configured payout accounts.
    Fields (bank_account / paypal_account) are omitted when not configured.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from app.administration.models import FeeConfiguration
        from decimal import Decimal as D

        user = request.user
        wallet = _get_or_create_wallet(user)

        config = FeeConfiguration.objects.first()
        if config:
            processing_fee        = str(config.withdraw_fee)
            processing_fee_pct    = str(config.withdraw_fee_percentage)
            min_withdraw_amount   = str(config.withdraw_min_amount)
        else:
            processing_fee        = str(getattr(settings, "WITHDRAW_FEE", "0.00"))
            processing_fee_pct    = str(getattr(settings, "WITHDRAW_FEE_PERCENTAGE", "0.00"))
            min_withdraw_amount   = "10.00"

        data = {
            "success": True,
            "wallet": WalletSerializer(wallet).data,
            "processing_fee": processing_fee,
            "processing_fee_percentage": processing_fee_pct,
            "min_withdraw_amount": min_withdraw_amount,
        }

        try:
            bank_account = BankAccount.objects.get(user=user)
            data["bank_account"] = BankAccountSerializer(bank_account).data
        except BankAccount.DoesNotExist:
            pass

        try:
            paypal_account = PaypalAccount.objects.get(user=user)
            data["paypal_account"] = PaypalAccountSerializer(paypal_account).data
        except PaypalAccount.DoesNotExist:
            pass

        return Response(data, status=status.HTTP_200_OK)


# ──────────────────────────────────────────────
# Withdraw Request
# ──────────────────────────────────────────────

class WithdrawRequestView(APIView):
    """
    POST — Submit a withdrawal request.

    Body:
        amount  (Decimal)  — amount the user wants to withdraw
        method  (str)      — "bank" or "paypal"

    Validations:
        1. amount >= min_withdraw_amount
        2. The chosen payout account must exist
        3. Wallet balance >= amount

    On success:
        - Deducts `amount` from wallet balance atomically
        - Creates WithdrawTransaction(status=PENDING) for admin approval
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from app.administration.models import FeeConfiguration
        from decimal import Decimal as D, ROUND_HALF_UP
        import uuid

        user   = request.user
        amount = request.data.get("amount")
        method = request.data.get("method", "").lower()

        # ── basic input validation ──────────────────────────────
        if not amount:
            return Response({"error": "amount is required."}, status=status.HTTP_400_BAD_REQUEST)
        if method not in ("bank", "paypal"):
            return Response({"error": "method must be 'bank' or 'paypal'."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            amount = D(str(amount)).quantize(D("0.01"), rounding=ROUND_HALF_UP)
        except Exception:
            return Response({"error": "Invalid amount."}, status=status.HTTP_400_BAD_REQUEST)

        if amount <= 0:
            return Response({"error": "Amount must be greater than zero."}, status=status.HTTP_400_BAD_REQUEST)

        # ── fee config ─────────────────────────────────────────
        config = FeeConfiguration.objects.first()
        if config:
            fixed_fee   = D(str(config.withdraw_fee))
            fee_pct     = D(str(config.withdraw_fee_percentage))
            min_amount  = D(str(config.withdraw_min_amount))
        else:
            fixed_fee   = D(str(getattr(settings, "WITHDRAW_FEE", "0.00")))
            fee_pct     = D(str(getattr(settings, "WITHDRAW_FEE_PERCENTAGE", "0.00")))
            min_amount  = D("10.00")

        # ── 1. minimum amount check ─────────────────────────────
        if amount < min_amount:
            return Response(
                {"error": f"Minimum withdrawal amount is ${min_amount}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── 2. payout account existence ─────────────────────────
        bank_obj   = None
        paypal_obj = None

        if method == "bank":
            try:
                bank_obj = BankAccount.objects.get(user=user)
            except BankAccount.DoesNotExist:
                return Response(
                    {"error": "No bank account configured. Please add one first."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            try:
                paypal_obj = PaypalAccount.objects.get(user=user)
            except PaypalAccount.DoesNotExist:
                return Response(
                    {"error": "No PayPal account configured. Please add one first."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # ── 3. balance check + atomic deduction ─────────────────
        fee        = (fixed_fee + (amount * fee_pct / D("100"))).quantize(D("0.01"), rounding=ROUND_HALF_UP)
        net_amount = (amount - fee).quantize(D("0.01"), rounding=ROUND_HALF_UP)

        with db_transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(user=user)

            if wallet.balance < amount:
                return Response(
                    {"error": "Insufficient wallet balance."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            wallet.balance -= amount
            wallet.save(update_fields=["balance", "updated_at"])

            txn = WithdrawTransaction.objects.create(
                user=user,
                method=method,
                amount=amount,
                fee=fee,
                net_amount=net_amount,
                status=WithdrawTransaction.Status.PENDING,
                transaction_ref=uuid.uuid4().hex[:20].upper(),
                description=f"Withdrawal via {method.capitalize()}",
                # bank-specific
                bank_name=bank_obj.bank_name if bank_obj else None,
                account_number_last4=bank_obj.account_number[-4:] if bank_obj else None,
                # paypal-specific
                paypal_email=paypal_obj.paypal_email if paypal_obj else None,
            )

        return Response(
            {
                "success": True,
                "message": "Withdrawal request submitted and is pending admin approval.",
                "transaction": {
                    "id": str(txn.id),
                    "method": txn.method,
                    "amount": str(txn.amount),
                    "fee": str(txn.fee),
                    "net_amount": str(txn.net_amount),
                    "status": txn.status,
                    "transaction_ref": txn.transaction_ref,
                    "created_at": txn.created_at,
                },
            },
            status=status.HTTP_201_CREATED,
        )



# ──────────────────────────────────────────────
# Stripe Fee Config
# ──────────────────────────────────────────────

class StripeFeeConfigView(APIView):
    """
    GET — Return the current Stripe fee percentage and fixed amount
    configured by the admin.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from app.administration.models import FeeConfiguration

        config = FeeConfiguration.objects.first()
        if config:
            fee_percent = str(config.stripe_fee_percentage)
            fixed_fee = str(config.stripe_fixed_fee)
        else:
            fee_percent = str(getattr(settings, "STRIPE_FEE_PERCENT", "3.00"))
            fixed_fee = "0.00"

        return Response(
            {
                "success": True,
                "stripe_fee_percentage": fee_percent,
                "stripe_fixed_fee": fixed_fee,
            },
            status=status.HTTP_200_OK,
        )


class WithdrawFeeConfigView(APIView):
    """
    GET — Return the current withdraw fee and withdraw fee percentage
    configured by the admin.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from app.administration.models import FeeConfiguration

        config = FeeConfiguration.objects.first()

        if config:
            withdraw_fee = str(config.withdraw_fee)
            withdraw_fee_percentage = str(config.withdraw_fee_percentage)
        else:
            withdraw_fee = str(getattr(settings, "WITHDRAW_FEE", "0.00"))
            withdraw_fee_percentage = str(getattr(settings, "WITHDRAW_FEE_PERCENTAGE", "0.00"))

        return Response(
            {
                "success": True,
                "withdraw_fee": withdraw_fee,
                "withdraw_fee_percentage": withdraw_fee_percentage,
            },
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
        elif event_type == "checkout.session.completed":
            self._handle_checkout_completed(data_object)

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

    @staticmethod
    def _handle_checkout_completed(session):
        metadata = session.get("metadata", {}) if isinstance(session, dict) else getattr(session, "metadata", {})
        if not metadata or metadata.get("type") != "subscription":
            return

        user_id = metadata.get("user_id")
        plan = metadata.get("plan")
        session_id = session.get("id") if isinstance(session, dict) else getattr(session, "id", None)

        from app.accounts.models import UserAccount, UserSubscription
        from django.utils import timezone
        from datetime import timedelta

        try:
            user = UserAccount.objects.get(id=user_id)
        except UserAccount.DoesNotExist:
            logger.warning("Webhook subscription: user not found for ID %s", user_id)
            return

        if plan == "monthly":
            duration = timedelta(days=30)
        else:
            duration = timedelta(days=365)

        active_until = timezone.now() + duration

        with db_transaction.atomic():
            sub, _ = UserSubscription.objects.update_or_create(
                user=user,
                defaults={
                    "plan": plan,
                    "stripe_session_id": session_id,
                    "active_until": active_until,
                    "is_active": True
                }
            )

        logger.info(
            "User %s subscription activated. Plan: %s, active until: %s",
            user.email, plan, active_until
        )


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


# ──────────────────────────────────────────────
# Profile Home
# ──────────────────────────────────────────────
class ProfileHome(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = ProfileHomeSerializer(request.user)
        return Response(serializer.data, status=status.HTTP_200_OK)


# ──────────────────────────────────────────────
# Payout Accounts
# ──────────────────────────────────────────────
class BankAccountView(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    generics.GenericAPIView
):
    serializer_class = BankAccountSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        try:
            return BankAccount.objects.get(user=self.request.user)
        except BankAccount.DoesNotExist:
            raise NotFound("Bank account not configured.")

    def get(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if BankAccount.objects.filter(user=request.user).exists():
            return Response(
                {"detail": "Bank account already exists. Use PATCH to update it."},
                status=status.HTTP_400_BAD_REQUEST
            )
        return self.create(request, *args, **kwargs)

    def patch(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        return self.destroy(request, *args, **kwargs)


class PaypalAccountView(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    generics.GenericAPIView
):
    serializer_class = PaypalAccountSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        try:
            return PaypalAccount.objects.get(user=self.request.user)
        except PaypalAccount.DoesNotExist:
            raise NotFound("PayPal account not configured.")

    def get(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if PaypalAccount.objects.filter(user=request.user).exists():
            return Response(
                {"detail": "PayPal account already exists. Use PATCH to update it."},
                status=status.HTTP_400_BAD_REQUEST
            )
        return self.create(request, *args, **kwargs)

    def patch(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        return self.destroy(request, *args, **kwargs)


# ──────────────────────────────────────────────
# Phone Number
# ──────────────────────────────────────────────
class UpdatePhoneNumberView(generics.UpdateAPIView):
    """
    PATCH or PUT to update the authenticated user's phone number.
    """
    serializer_class = PhoneNumberSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


class UpdateProfileView(generics.UpdateAPIView):
    """
    PATCH or PUT to update the authenticated user's full name and/or profile picture.
    Accepts multipart/form-data (for file uploads).
    """
    serializer_class = ProfileUpdateSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    def get_object(self):
        return self.request.user


# ──────────────────────────────────────────────
# Withdraw History
# ──────────────────────────────────────────────

class PaypalWithdrawHistoryView(APIView):
    """
    GET — Paginated list of the authenticated user's PayPal withdrawal history.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        transactions = WithdrawTransaction.objects.filter(
            user=request.user,
            method=WithdrawTransaction.Method.PAYPAL,
        )

        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(transactions, request, view=self)

        serializer = PaypalWithdrawHistorySerializer(page, many=True)
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


class BankWithdrawHistoryView(APIView):
    """
    GET — Paginated list of the authenticated user's Bank withdrawal history.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        transactions = WithdrawTransaction.objects.filter(
            user=request.user,
            method=WithdrawTransaction.Method.BANK,
        )

        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(transactions, request, view=self)

        serializer = BankWithdrawHistorySerializer(page, many=True)
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


class CreateSubscriptionSessionView(APIView):
    """
    POST — Create a Stripe Checkout Session for subscription paywall.
    Accepts: { "plan": "monthly" | "yearly" }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        plan = request.data.get("plan", "monthly").lower()
        if plan not in ("monthly", "yearly"):
            return Response({"error": "plan must be 'monthly' or 'yearly'."}, status=status.HTTP_400_BAD_REQUEST)

        # 50% discount if payment yearly:
        # Monthly is $2 USD (lasts 30 days)
        # Yearly is $12 USD (lasts 365 days)
        if plan == "monthly":
            amount_cents = 200  # $2.00
            plan_name = "Monthly Subscription Plan"
        else:
            amount_cents = 1200  # $12.00
            plan_name = "Yearly Subscription Plan (50% Off)"

        # We can dynamically point to the frontend referrer or use default localhost
        referer = request.META.get("HTTP_REFERER")
        if referer:
            from urllib.parse import urlparse
            parsed = urlparse(referer)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
        else:
            base_url = "http://localhost:3000"

        success_url = f"{base_url}/dashboard/escrows?subscription=success"
        cancel_url = f"{base_url}/dashboard/escrows?subscription=cancel"

        try:
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[
                    {
                        "price_data": {
                            "currency": "usd",
                            "product_data": {
                                "name": plan_name,
                                "description": "Access unlimited escrows on PayParo",
                            },
                            "unit_amount": amount_cents,
                        },
                        "quantity": 1,
                    }
                ],
                mode="payment",
                metadata={
                    "user_id": str(request.user.id),
                    "plan": plan,
                    "type": "subscription",
                },
                success_url=success_url,
                cancel_url=cancel_url,
            )
            return Response(
                {
                    "success": True,
                    "checkout_url": session.url,
                    "session_id": session.id,
                },
                status=status.HTTP_200_OK,
            )
        except stripe.error.StripeError as e:
            logger.error("Stripe error creating Checkout Session: %s", e)
            return Response(
                {"error": "Payment checkout service currently unavailable."},
                status=status.HTTP_502_BAD_GATEWAY,
            )


class UserSubscriptionStatusView(APIView):
    """
    GET — Retrieve the current user's subscription status, active until,
    and number of escrows created.
    POST — Directly activate subscription via local app SDK payment.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from app.excrow.models import Escrow
        from django.utils import timezone

        user = request.user
        created_count = Escrow.objects.filter(created_by=user).count()

        is_sub = user.is_subscribed
        sub_details = None

        try:
            sub = user.subscription
            sub_details = {
                "plan": sub.plan,
                "active_until": sub.active_until,
                "is_active": sub.is_active and sub.active_until > timezone.now(),
            }
        except Exception:
            pass

        return Response(
            {
                "success": True,
                "is_subscribed": is_sub,
                "created_escrow_count": created_count,
                "subscription": sub_details,
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        from app.accounts.models import UserSubscription
        from app.excrow.models import Escrow
        from django.utils import timezone
        from datetime import timedelta

        plan = request.data.get("plan", "monthly").lower()
        if plan not in ("monthly", "yearly"):
            return Response({"error": "plan must be 'monthly' or 'yearly'."}, status=status.HTTP_400_BAD_REQUEST)

        days = 30 if plan == "monthly" else 365

        user = request.user
        sub, created = UserSubscription.objects.get_or_create(user=user)
        sub.plan = plan
        sub.active_until = timezone.now() + timedelta(days=days)
        sub.is_active = True
        sub.stripe_session_id = "in_app_payment_sdk"
        sub.save()

        created_count = Escrow.objects.filter(created_by=user).count()

        return Response(
            {
                "success": True,
                "is_subscribed": True,
                "created_escrow_count": created_count,
                "subscription": {
                    "plan": sub.plan,
                    "active_until": sub.active_until,
                    "is_active": True,
                }
            },
            status=status.HTTP_200_OK,
        )
