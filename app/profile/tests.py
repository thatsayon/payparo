from decimal import Decimal
import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from app.accounts.models import UserAccount
from app.profile.models import Wallet, WalletTransaction


class StripeWebhookTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = UserAccount.objects.create_user(
            email="webhook_tester@example.com",
            password="testpassword123",
            full_name="Webhook Tester",
        )
        self.wallet, _ = Wallet.objects.get_or_create(user=self.user)
        self.webhook_url = "/api/profile/wallet/webhook/stripe/"

    @override_settings(STRIPE_WEBHOOK_SECRET="")
    def test_webhook_payment_intent_succeeded_existing_transaction(self):
        txn = WalletTransaction.objects.create(
            wallet=self.wallet,
            transaction_type=WalletTransaction.TransactionType.DEPOSIT,
            amount=Decimal("50.00"),
            fee=Decimal("1.50"),
            total_charged=Decimal("51.50"),
            stripe_payment_intent_id="pi_test_existing_123",
            status=WalletTransaction.Status.PENDING,
        )

        payload = {
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_test_existing_123",
                    "amount": 5150,
                }
            },
        }

        response = self.client.post(
            self.webhook_url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        txn.refresh_from_db()
        self.assertEqual(txn.status, WalletTransaction.Status.COMPLETED)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("50.00"))

    @override_settings(STRIPE_WEBHOOK_SECRET="")
    def test_webhook_payment_intent_succeeded_direct_client_fallback(self):
        payload = {
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_test_direct_fallback_456",
                    "amount": 2060,  # $20 + 3% fee
                    "metadata": {
                        "user_id": str(self.user.id),
                        "wallet_amount": "20.00",
                    },
                }
            },
        }

        response = self.client.post(
            self.webhook_url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        txn = WalletTransaction.objects.filter(
            stripe_payment_intent_id="pi_test_direct_fallback_456"
        ).first()
        self.assertIsNotNone(txn)
        self.assertEqual(txn.status, WalletTransaction.Status.COMPLETED)
        self.assertEqual(txn.amount, Decimal("20.00"))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("20.00"))

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_dummy_secret")
    def test_webhook_invalid_signature_when_secret_set(self):
        payload = {"type": "ping", "data": {"object": {}}}
        response = self.client.post(
            self.webhook_url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="bad_signature",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)
