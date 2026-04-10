import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.accounts.models import KYCSubmission
from app.profile.models import Wallet, WalletTransaction


User = get_user_model()


class UserManagementViewTests(TestCase):
	def setUp(self):
		self.staff_user = User.objects.create_user(
			email="admin@example.com",
			password="pass1234",
			full_name="Admin User",
			is_staff=True,
		)
		self.approved_user = User.objects.create_user(
			email="emma@example.com",
			password="pass1234",
			full_name="Emma Radi",
		)
		self.pending_user = User.objects.create_user(
			email="john@example.com",
			password="pass1234",
			full_name="John Smith",
		)

		KYCSubmission.objects.create(user=self.approved_user, status=KYCSubmission.Status.APPROVED)
		KYCSubmission.objects.create(user=self.pending_user, status=KYCSubmission.Status.PENDING)

		approved_wallet = Wallet.objects.create(user=self.approved_user, balance=100)
		WalletTransaction.objects.create(
			wallet=approved_wallet,
			transaction_type=WalletTransaction.TransactionType.DEPOSIT,
			amount=10,
			fee=0,
			total_charged=10,
			status=WalletTransaction.Status.COMPLETED,
			description="Test deposit",
		)

	def test_view_returns_serialized_payload_for_staff_users(self):
		self.client.force_login(self.staff_user)

		response = self.client.get(reverse("user-management"))
		payload = json.loads(response.content)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(payload["page_title"], "User Management")
		self.assertEqual(payload["total_users"], 3)
		self.assertEqual(payload["users"][0]["full_name"], "Admin User")
		self.assertTrue(any(user["full_name"] == "Emma Radi" for user in payload["users"]))
		self.assertTrue(any(user["kyc_label"] == "Approved" for user in payload["users"]))

	def test_status_filter_limits_results(self):
		self.client.force_login(self.staff_user)

		response = self.client.get(reverse("user-management"), {"status": "approved"})
		payload = json.loads(response.content)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(payload["selected_status"], "approved")
		self.assertEqual(len(payload["users"]), 1)
		self.assertEqual(payload["users"][0]["full_name"], "Emma Radi")

	def test_non_staff_is_denied(self):
		self.client.force_login(self.pending_user)

		response = self.client.get(reverse("user-management"))

		self.assertEqual(response.status_code, 403)
