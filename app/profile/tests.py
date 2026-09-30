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


class SubscriptionEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = UserAccount.objects.create_user(
            email="subscriber@example.com",
            password="testpassword123",
            full_name="Subscriber Tester",
        )
        self.client.force_authenticate(user=self.user)
        self.create_intent_url = "/api/profile/wallet/subscription/create-intent/"
        self.status_url = "/api/profile/wallet/subscription/status/"
        self.profile_home_url = "/api/profile/home/"

    @patch("stripe.PaymentIntent.create")
    def test_create_subscription_intent_yearly(self, mock_create):
        class MockIntent:
            id = "pi_sub_12345"
            client_secret = "pi_sub_12345_secret_test"

        mock_create.return_value = MockIntent()

        response = self.client.post(
            self.create_intent_url,
            data={"plan": "yearly"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["client_secret"], "pi_sub_12345_secret_test")
        self.assertEqual(response.data["payment_intent_id"], "pi_sub_12345")
        self.assertEqual(response.data["amount"], 12.0)
        self.assertEqual(response.data["plan"], "yearly")

    def test_subscription_status_flow(self):
        # 1. Initially not subscribed
        get_res = self.client.get(self.status_url)
        self.assertEqual(get_res.status_code, status.HTTP_200_OK)
        self.assertFalse(get_res.data["is_subscribed"])

        # 2. Activate subscription
        post_res = self.client.post(
            self.status_url,
            data={"plan": "yearly", "payment_intent_id": "test_pi_yearly"},
            format="json",
        )
        self.assertEqual(post_res.status_code, status.HTTP_200_OK)
        self.assertTrue(post_res.data["is_subscribed"])
        self.assertEqual(post_res.data["subscription"]["plan"], "yearly")

        # 3. Check profile home serializer includes is_subscribed
        home_res = self.client.get(self.profile_home_url)
        self.assertEqual(home_res.status_code, status.HTTP_200_OK)
        self.assertTrue(home_res.data["is_subscribed"])

    @override_settings(STRIPE_WEBHOOK_SECRET="")
    def test_webhook_activates_subscription(self):
        payload = {
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_webhook_sub_789",
                    "amount": 200,
                    "metadata": {
                        "type": "subscription",
                        "plan": "monthly",
                        "user_id": str(self.user.id),
                    },
                }
            },
        }

        response = self.client.post(
            "/api/profile/wallet/webhook/stripe/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.user.refresh_from_db()
        self.assertTrue(self.user.is_subscribed)
        self.assertEqual(self.user.subscription.plan, "monthly")

