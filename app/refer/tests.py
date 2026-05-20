from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from rest_framework.test import APITestCase
from rest_framework import status
from django.urls import reverse

from app.refer.models import ReferralProfile, ReferralEarning
from app.excrow.models import Escrow, EscrowStatusHistory
from app.profile.models import Wallet, WalletTransaction
from app.refer.tasks import process_pending_referrals

from django.test import override_settings

User = get_user_model()


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_BROKER_URL="memory://")
class ReferralSystemTests(APITestCase):

    def setUp(self):
        # Create users
        self.referrer = User.objects.create_user(
            email="referrer@example.com",
            password="password123",
            full_name="Referrer User"
        )
        self.referred = User.objects.create_user(
            email="referred@example.com",
            password="password123",
            full_name="Referred User"
        )
        
        # Ensure Profiles exist
        self.referrer_profile, _ = ReferralProfile.objects.get_or_create(user=self.referrer)
        self.referred_profile, _ = ReferralProfile.objects.get_or_create(user=self.referred)
        
        # Set custom referral code for referrer
        self.referrer_profile.referral_code = "my-promo-code"
        self.referrer_profile.save()

    def test_signup_with_referral_code(self):
        """Tests registering a new user with an optional referral code."""
        url = reverse("register")
        data = {
            "email": "newuser@example.com",
            "password": "securepassword",
            "password_confirm": "securepassword",
            "full_name": "New Referred User",
            "referral_code": "my-promo-code"
        }
        
        response = self.client.post(url, data)
        if response.status_code != status.HTTP_201_CREATED:
            print("SIGNUP RESPONSE ERROR DATA:", response.data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Verify the new user's referral relationship
        new_user = User.objects.get(email="newuser@example.com")
        new_profile = new_user.referral_profile
        self.assertEqual(new_profile.referred_by, self.referrer)
        self.assertIsNotNone(new_profile.referred_at)
        
        # Verify pending ReferralEarning record
        earning = ReferralEarning.objects.get(referrer=self.referrer, referred_user=new_user)
        self.assertEqual(earning.status, ReferralEarning.Status.PENDING)
        self.assertEqual(earning.amount, ReferralProfile.REFERRAL_COMMISSION_AMOUNT)
        self.assertIsNone(earning.escrow)

    def test_apply_referral_code_post_signup(self):
        """Tests applying a referral code post-registration."""
        self.client.force_authenticate(user=self.referred)
        
        # 1. Test applying an invalid code
        response = self.client.post(reverse("referral-apply"), {"referral_code": "nonexistent"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        
        # 2. Test referring oneself
        response = self.client.post(reverse("referral-apply"), {"referral_code": self.referred_profile.referral_code})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        
        # 3. Apply a valid code successfully
        response = self.client.post(reverse("referral-apply"), {"referral_code": "my-promo-code"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Verify relationship and earning
        self.referred_profile.refresh_from_db()
        self.assertEqual(self.referred_profile.referred_by, self.referrer)
        
        earning = ReferralEarning.objects.get(referrer=self.referrer, referred_user=self.referred)
        self.assertEqual(earning.status, ReferralEarning.Status.PENDING)

    def test_dashboard_and_code_customization(self):
        """Tests the referral dashboard endpoint and updating referral codes."""
        self.client.force_authenticate(user=self.referrer)
        
        # 1. Retrieve the dashboard
        url = reverse("referral-dashboard")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["referral_code"], "my-promo-code")
        
        # 2. Update custom referral code with invalid format
        response = self.client.put(url, {"referral_code": "invalid space"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        
        # 3. Update custom referral code successfully
        response = self.client.put(url, {"referral_code": "super-promo-99"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["referral_code"], "super-promo-99")
        
        self.referrer_profile.refresh_from_db()
        self.assertEqual(self.referrer_profile.referral_code, "super-promo-99")

    def test_escrow_delivery_linking_and_auto_approval_cycle(self):
        """Tests that a delivered escrow is linked and approved after 48 hours."""
        # 1. Link referrer and referred user
        self.referred_profile.referred_by = self.referrer
        self.referred_profile.referred_at = timezone.now()
        self.referred_profile.save()
        
        earning = ReferralEarning.objects.create(
            referrer=self.referrer,
            referred_user=self.referred,
            amount=ReferralProfile.REFERRAL_COMMISSION_AMOUNT,
            status=ReferralEarning.Status.PENDING
        )
        
        # 2. Create an escrow transaction where the referred user is the buyer
        escrow = Escrow.objects.create(
            created_by=self.referrer,
            receiver=self.referred,
            role=Escrow.Role.SELLER,
            product_name="Premium Package Deal",
            description="Premium service contract",
            price=150.00,
            fee_amount=5.00,
            total_amount=155.00,
            status=Escrow.Status.IN_PROGRESS
        )
        
        # 3. Transition escrow to DELIVERED
        escrow.status = Escrow.Status.DELIVERED
        escrow.save() # Triggers post-save signal linking the escrow
        
        earning.refresh_from_db()
        self.assertEqual(earning.escrow, escrow)
        self.assertEqual(earning.status, ReferralEarning.Status.PENDING)
        
        # 4. Trigger process_pending_referrals immediately (should remain pending since 48 hours haven't passed)
        process_pending_referrals(referrer=self.referrer)
        earning.refresh_from_db()
        self.assertEqual(earning.status, ReferralEarning.Status.PENDING)
        
        # 5. Backdate the DELIVERED status history timestamp by 49 hours to simulate elapsed time
        delivery_history = EscrowStatusHistory.objects.get(escrow=escrow, status=Escrow.Status.DELIVERED)
        delivery_history.created_at = timezone.now() - timedelta(hours=49)
        delivery_history.save()
        
        # 6. Run process_pending_referrals again (should now auto-approve and credit wallet)
        process_pending_referrals(referrer=self.referrer)
        
        earning.refresh_from_db()
        self.assertEqual(earning.status, ReferralEarning.Status.COMPLETED)
        
        # Verify profile and wallet credits
        self.referrer_profile.refresh_from_db()
        self.assertEqual(float(self.referrer_profile.total_earnings), 10.00)
        
        wallet = Wallet.objects.get(user=self.referrer)
        self.assertEqual(float(wallet.balance), 10.00)
        
        tx = WalletTransaction.objects.get(wallet=wallet)
        self.assertEqual(tx.status, WalletTransaction.Status.COMPLETED)
        self.assertEqual(float(tx.amount), 10.00)
