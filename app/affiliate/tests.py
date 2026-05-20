from decimal import Decimal
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from app.accounts.models import UserAccount
from app.excrow.models import Escrow
from app.affiliate.models import (
    AffiliateApplication,
    AffiliateProfile,
    AffiliateAttribution,
    AffiliateReward,
    AffiliateWithdrawal,
    AffiliateClick,
    AffiliateFraudFlag,
    AffiliateGlobalBudget,
)
from app.affiliate.tasks import release_held_affiliate_rewards, recalculate_affiliate_tiers

User = get_user_model()


class AffiliateSystemTests(APITestCase):

    def setUp(self):
        # Create superadmin / staff user
        self.admin_user = User.objects.create_superuser(
            email="admin@payparo.com",
            password="adminpassword123",
            full_name="Admin User",
        )
        # Create standard users
        self.affiliate_user = User.objects.create_user(
            email="affiliate@partner.com",
            password="partnerpassword",
            full_name="Partner Bob",
            role=UserAccount.Role.AFFILIATE,
        )
        self.referred_user = User.objects.create_user(
            email="referred@buyer.com",
            password="buyerpassword",
            full_name="Buyer Alice",
        )

        # Create affiliate profile
        self.affiliate_profile = AffiliateProfile.objects.create(
            user=self.affiliate_user,
            slug="bob-deals",
        )

        # Global budget singleton
        self.budget = AffiliateGlobalBudget.get_singleton()

    def test_affiliate_application_flow(self):
        """Test submitting application, reviewing, and approving/creating a profile."""
        self.client.force_authenticate(user=self.admin_user)

        # 1. Submit application (anonymous/anyone)
        self.client.force_authenticate(user=None)
        apply_url = reverse("affiliate-apply")
        data = {
            "full_name": "Dave Discord",
            "email": "dave@discord.com",
            "phone": "5551234567",
            "platform": "discord",
            "community_name": "Dave's Crypto Corner",
            "community_url": "https://discord.gg/dave",
            "community_member_count": 15000,
            "community_description": "Active Discord channel with 15k members",
            "desired_slug": "dave-crypto",
            "tax_id": "RFC-DAVE990101",
            "business_name": "Dave Media SA",
            "country": "MX",
            "bank_name": "BBVA",
            "clabe": "012345678901234567",
            "account_holder_name": "Dave Discord",
        }
        res = self.client.post(apply_url, data)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        app_id = res.data["id"]

        # Check in DB
        app = AffiliateApplication.objects.get(pk=app_id)
        self.assertEqual(app.status, AffiliateApplication.Status.PENDING)

        # 2. Admin approves application with a vanity slug
        self.client.force_authenticate(user=self.admin_user)
        approve_url = reverse("admin-affiliate-status", kwargs={"pk": app_id})
        res = self.client.patch(approve_url, {"status": "approved", "slug": "dave-crypto"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Verify profile and User role got created/updated
        app.refresh_from_db()
        self.assertEqual(app.status, AffiliateApplication.Status.APPROVED)
        self.assertIsNotNone(app.user)
        self.assertEqual(app.user.role, UserAccount.Role.AFFILIATE)

        profile = AffiliateProfile.objects.get(user=app.user)
        self.assertEqual(profile.slug, "dave-crypto")
        self.assertTrue(profile.is_active)

    def test_referral_click_tracking_cookie(self):
        """Test the public redirect logs click and sets pp_aff attribution cookie."""
        slug = self.affiliate_profile.slug
        url = reverse("affiliate-click", kwargs={"slug": slug})
        res = self.client.get(url + "?fp=fingerprint123")

        self.assertEqual(res.status_code, status.HTTP_302_FOUND)
        self.assertIn("pp_aff", res.cookies)
        cookie_val = res.cookies["pp_aff"].value
        self.assertTrue(cookie_val.startswith(f"{self.affiliate_profile.affiliate_id}:"))

        # Verify Click record in DB
        click = AffiliateClick.objects.first()
        self.assertIsNotNone(click)
        self.assertEqual(click.affiliate, self.affiliate_profile)
        self.assertEqual(click.device_fingerprint, "fingerprint123")

    def test_commission_generation_on_escrow_completed(self):
        """Test recurring commission and activation bonus generation when an escrow is completed."""
        # 1. Attribute user to affiliate
        click = AffiliateClick.objects.create(affiliate=self.affiliate_profile)
        attribution = AffiliateAttribution.objects.create(
            affiliate=self.affiliate_profile,
            referred_user=self.referred_user,
            click=click,
        )

        # 2. Complete escrow transaction
        escrow = Escrow.objects.create(
            created_by=self.referred_user,
            receiver=self.admin_user,
            price=Decimal("1000.00"),
            status=Escrow.Status.COMPLETED,
        )

        # Manually trigger signal logic (Django APITestCase handles signals, but in tests let's verify database state)
        from app.affiliate.signals import _generate_affiliate_rewards
        _generate_affiliate_rewards(escrow)

        # 3. Check generated rewards
        rewards = AffiliateReward.objects.filter(affiliate=self.affiliate_profile)
        self.assertEqual(rewards.count(), 2)  # 1 recurring commission + 1 activation bonus

        # Check recurring commission calculation
        recurring = rewards.get(reward_type=AffiliateReward.RewardType.RECURRING_COMMISSION)
        self.assertEqual(recurring.state, AffiliateReward.State.PENDING_HOLD)
        # Platform fee = 3% of 1000 + 1 USD (17.5 MXN) = 30 + 17.5 = 47.5 MXN
        # Affiliate commission = 30% of 47.5 = 14.25 MXN
        self.assertEqual(recurring.amount, Decimal("14.25"))

        # Check activation bonus (one-time 200 MXN)
        bonus = rewards.get(reward_type=AffiliateReward.RewardType.ACTIVATION_BONUS)
        self.assertEqual(bonus.amount, Decimal("200.00"))

        # Verify pending balance updated
        self.affiliate_profile.refresh_from_db()
        self.assertEqual(self.affiliate_profile.total_pending_hold, Decimal("214.25"))
        self.assertEqual(self.affiliate_profile.withdrawable_balance, Decimal("0.00"))

    def test_release_held_rewards_celery_task(self):
        """Test release held rewards moving from PENDING_HOLD to RELEASED after clearing period."""
        # Create a reward that is ready for release (hold_until is in the past)
        reward = AffiliateReward.objects.create(
            affiliate=self.affiliate_profile,
            reward_type=AffiliateReward.RewardType.RECURRING_COMMISSION,
            state=AffiliateReward.State.PENDING_HOLD,
            amount=Decimal("100.00"),
            platform_fee=Decimal("300.00"),
            hold_until=timezone.now() - timezone.timedelta(days=1),
            idempotency_key="idemp-1",
        )
        self.affiliate_profile.total_pending_hold = Decimal("100.00")
        self.affiliate_profile.save()

        # Run task
        release_held_affiliate_rewards()

        # Verify
        reward.refresh_from_db()
        self.assertEqual(reward.state, AffiliateReward.State.RELEASED)

        self.affiliate_profile.refresh_from_db()
        self.assertEqual(self.affiliate_profile.total_pending_hold, Decimal("0.00"))
        self.assertEqual(self.affiliate_profile.withdrawable_balance, Decimal("100.00"))
        self.assertEqual(self.affiliate_profile.total_released, Decimal("100.00"))

    def test_fraud_signals_detection(self):
        """Test detecting self-referral and shared IP fraud signals."""
        from app.affiliate.utils import detect_fraud_signals

        # Self-referral check
        signals = detect_fraud_signals(self.affiliate_profile, self.affiliate_user)
        self.assertIn("self_referral", signals)

        # Same IP check
        click_aff = AffiliateClick.objects.create(affiliate=self.affiliate_profile, ip_address="192.168.1.1")
        click_ref = AffiliateClick.objects.create(affiliate=self.affiliate_profile, ip_address="192.168.1.1")
        attribution = AffiliateAttribution.objects.create(
            affiliate=self.affiliate_profile,
            referred_user=self.referred_user,
            click=click_ref,
        )
        signals = detect_fraud_signals(self.affiliate_profile, self.referred_user)
        self.assertIn("same_ip", signals)
