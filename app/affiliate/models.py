import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from cloudinary.models import CloudinaryField
from app.common.models import BaseModel


# ─────────────────────────────────────────────────────────────────────────────
# Affiliate Application
# ─────────────────────────────────────────────────────────────────────────────

class AffiliateApplication(BaseModel):
    """
    Submitted by a Telegram/Discord admin who wants to become an affiliate.
    Staff manually review and approve. One application per user.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        SUSPENDED = "suspended", "Suspended"

    class Platform(models.TextChoices):
        TELEGRAM = "telegram", "Telegram"
        DISCORD = "discord", "Discord"
        BOTH = "both", "Both"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="affiliate_application",
        null=True,
        blank=True,
        help_text="Set when the application is created by an existing user. May be null for pre-creation applications.",
    )

    # ── Contact & Identity ────────────────────────────────────────────────────
    full_name = models.CharField(max_length=120)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=30, blank=True)

    # ── Social / Community Proof ──────────────────────────────────────────────
    platform = models.CharField(max_length=10, choices=Platform.choices)
    community_name = models.CharField(max_length=200, help_text="e.g. SNEAKERHEADS_MX")
    community_url = models.URLField(help_text="Invite link or public profile URL")
    community_member_count = models.PositiveIntegerField(default=0)
    community_description = models.TextField(blank=True)

    # ── Vanity Slug (admin sets this on approval) ─────────────────────────────
    desired_slug = models.CharField(max_length=80, blank=True, help_text="Applicant's preferred slug, subject to admin approval")

    # ── Tax / Business Info ───────────────────────────────────────────────────
    tax_id = models.CharField(max_length=50, blank=True, help_text="RFC / Business Number")
    business_name = models.CharField(max_length=200, blank=True)
    country = models.CharField(max_length=80, default="MX")

    # ── Bank Info (SPEI) ──────────────────────────────────────────────────────
    bank_name = models.CharField(max_length=100, blank=True)
    clabe = models.CharField(max_length=18, blank=True, help_text="18-digit CLABE for SPEI")
    account_holder_name = models.CharField(max_length=200, blank=True)
    password_hash = models.CharField(max_length=255, blank=True, null=True)

    # ── Documents (Cloudinary) ────────────────────────────────────────────────
    id_document = CloudinaryField(blank=True, null=True, resource_type="auto", help_text="Government-issued ID")
    tax_document = CloudinaryField(blank=True, null=True, resource_type="auto", help_text="Tax certificate / RFC document")
    bank_statement = CloudinaryField(blank=True, null=True, resource_type="auto", help_text="Recent bank statement")

    # ── Status ────────────────────────────────────────────────────────────────
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING, db_index=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="reviewed_affiliate_applications",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["email"]),
        ]

    def __str__(self):
        return f"AffiliateApplication({self.email}) — {self.status}"


# ─────────────────────────────────────────────────────────────────────────────
# Affiliate Profile (created on approval)
# ─────────────────────────────────────────────────────────────────────────────

def generate_affiliate_id():
    return uuid.uuid4().hex[:12].upper()


class AffiliateProfile(BaseModel):
    """
    Created by staff when an AffiliateApplication is approved.
    Holds the live referral slug and commission tier.
    """

    class Tier(models.TextChoices):
        BASE = "base", "Base (30%)"
        ELEVATED = "elevated", "Elevated (40%)"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="affiliate_profile",
    )
    application = models.OneToOneField(
        AffiliateApplication,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="profile",
    )

    affiliate_id = models.CharField(max_length=20, unique=True, default=generate_affiliate_id, db_index=True)
    slug = models.SlugField(max_length=80, unique=True, help_text="Vanity slug: payparo.com/p/<slug>")

    # ── Commission Tier ───────────────────────────────────────────────────────
    tier = models.CharField(max_length=10, choices=Tier.choices, default=Tier.BASE)

    # ── Ledger Balances (denormalised for fast reads) ─────────────────────────
    total_earned = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total_pending_hold = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total_released = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total_paid_out = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    # Can go negative after post-payout chargebacks
    withdrawable_balance = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["affiliate_id"]),
        ]

    @property
    def referral_url(self):
        from django.conf import settings as django_settings
        base = getattr(django_settings, "SITE_BASE_URL", "https://payparo.com")
        return f"{base}/p/{self.slug}"

    def __str__(self):
        return f"AffiliateProfile({self.user.email}) slug={self.slug} tier={self.tier}"


# ─────────────────────────────────────────────────────────────────────────────
# Click Tracking
# ─────────────────────────────────────────────────────────────────────────────

class AffiliateClick(models.Model):
    """Immutable log of every referral link click."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    affiliate = models.ForeignKey(AffiliateProfile, on_delete=models.CASCADE, related_name="clicks")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    device_fingerprint = models.CharField(max_length=128, blank=True, db_index=True)
    clicked_at = models.DateTimeField(auto_now_add=True, db_index=True)
    converted = models.BooleanField(default=False, help_text="True after the visitor signs up")

    class Meta:
        ordering = ["-clicked_at"]
        indexes = [
            models.Index(fields=["affiliate", "clicked_at"]),
            models.Index(fields=["device_fingerprint"]),
        ]

    def __str__(self):
        return f"Click on {self.affiliate.slug} at {self.clicked_at}"


# ─────────────────────────────────────────────────────────────────────────────
# Attribution — permanent link between referred user & affiliate
# ─────────────────────────────────────────────────────────────────────────────

class AffiliateAttribution(BaseModel):
    """
    First-touch attribution. Created once when an attributed user signs up.
    Immutable after creation — never change referred_user or affiliate.
    """
    affiliate = models.ForeignKey(AffiliateProfile, on_delete=models.CASCADE, related_name="attributions")
    referred_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="affiliate_attribution",
    )
    click = models.ForeignKey(AffiliateClick, on_delete=models.SET_NULL, null=True, blank=True)
    attributed_at = models.DateTimeField(default=timezone.now)

    # ── First-transaction tracking ────────────────────────────────────────────
    first_transaction_discount_used = models.BooleanField(default=False)
    first_transaction_discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    activation_bonus_paid = models.BooleanField(default=False, help_text="200 MXN one-time bonus paid to affiliate")

    # ── Fraud check ───────────────────────────────────────────────────────────
    fraud_flagged = models.BooleanField(default=False)

    class Meta:
        ordering = ["-attributed_at"]
        indexes = [
            models.Index(fields=["affiliate"]),
            models.Index(fields=["referred_user"]),
        ]

    def __str__(self):
        return f"Attribution: {self.referred_user.email} → {self.affiliate.slug}"


# ─────────────────────────────────────────────────────────────────────────────
# Affiliate Reward (Ledger)
# ─────────────────────────────────────────────────────────────────────────────

class AffiliateReward(BaseModel):
    """
    Immutable ledger entry for every affiliate reward event.
    States: pending_hold → released (after 14 days) | voided | deducted
    """

    class RewardType(models.TextChoices):
        RECURRING_COMMISSION = "recurring_commission", "Recurring Commission"
        ACTIVATION_BONUS = "activation_bonus", "Activation Bonus (200 MXN)"
        DEDUCTION = "deduction", "Post-Payout Deduction"

    class State(models.TextChoices):
        PENDING_HOLD = "pending_hold", "Pending Hold (14 days)"
        RELEASED = "released", "Released"
        VOIDED = "voided", "Voided"
        DEDUCTED = "deducted", "Deducted"

    affiliate = models.ForeignKey(AffiliateProfile, on_delete=models.CASCADE, related_name="rewards")
    attribution = models.ForeignKey(AffiliateAttribution, on_delete=models.SET_NULL, null=True, blank=True, related_name="rewards")
    escrow = models.ForeignKey(
        "excrow.Escrow", on_delete=models.SET_NULL, null=True, blank=True, related_name="affiliate_rewards"
    )

    reward_type = models.CharField(max_length=25, choices=RewardType.choices)
    state = models.CharField(max_length=15, choices=State.choices, default=State.PENDING_HOLD, db_index=True)
    currency = models.CharField(max_length=5, default="MXN")

    # Gross platform fee for this escrow (informational)
    platform_fee = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    # Commission rate applied (e.g. 0.30 or 0.40)
    commission_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.30"))
    # The actual reward amount
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    hold_until = models.DateTimeField(null=True, blank=True, help_text="When pending_hold expires and reward is released")
    released_at = models.DateTimeField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=255, blank=True)

    # Idempotency key to prevent duplicate reward creation
    idempotency_key = models.CharField(max_length=100, unique=True, db_index=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["affiliate", "state"]),
            models.Index(fields=["escrow"]),
            models.Index(fields=["hold_until"]),
        ]

    def save(self, *args, **kwargs):
        if not self.idempotency_key:
            escrow_id = str(self.escrow_id) if self.escrow_id else "none"
            self.idempotency_key = f"{self.affiliate_id}:{self.reward_type}:{escrow_id}"
        if not self.hold_until and self.state == self.State.PENDING_HOLD:
            self.hold_until = timezone.now() + timezone.timedelta(days=14)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"AffiliateReward {self.reward_type} {self.amount} MXN [{self.state}] for {self.affiliate.slug}"


# ─────────────────────────────────────────────────────────────────────────────
# Affiliate Withdrawal (Payout Request)
# ─────────────────────────────────────────────────────────────────────────────

class AffiliateWithdrawal(BaseModel):
    """
    Affiliate-submitted payout request. Requires admin approval.
    Payout via SPEI bank transfer.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending Review"
        APPROVED = "approved", "Approved"
        COMPLETED = "completed", "Completed"
        REJECTED = "rejected", "Rejected"

    affiliate = models.ForeignKey(AffiliateProfile, on_delete=models.CASCADE, related_name="withdrawals")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=5, default="MXN")

    # ── SPEI details (snapshot at time of request) ────────────────────────────
    bank_name = models.CharField(max_length=100)
    clabe = models.CharField(max_length=18)
    account_holder_name = models.CharField(max_length=200)

    # ── CFDI Invoice ──────────────────────────────────────────────────────────
    cfdi_invoice = CloudinaryField(resource_type="auto", null=True, blank=True, help_text="CFDI invoice PDF")
    cfdi_invoice_number = models.CharField(max_length=100, blank=True)

    # ── ISR Withholding ───────────────────────────────────────────────────────
    isr_withholding = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    net_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    # ── Admin Processing ──────────────────────────────────────────────────────
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING, db_index=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="reviewed_affiliate_withdrawals",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    transaction_ref = models.CharField(max_length=100, blank=True, help_text="SPEI transaction reference")
    admin_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["affiliate", "status"]),
        ]

    def __str__(self):
        return f"Withdrawal {self.amount} MXN by {self.affiliate.slug} [{self.status}]"


# ─────────────────────────────────────────────────────────────────────────────
# Volume Tier History
# ─────────────────────────────────────────────────────────────────────────────

class AffiliateTierHistory(BaseModel):
    """Monthly snapshot of tier, volume, and commission rate for an affiliate."""
    affiliate = models.ForeignKey(AffiliateProfile, on_delete=models.CASCADE, related_name="tier_history")
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()  # 1–12
    monthly_volume = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0.00"))
    tier_applied = models.CharField(max_length=10, choices=AffiliateProfile.Tier.choices)
    commission_rate = models.DecimalField(max_digits=5, decimal_places=4)

    class Meta:
        unique_together = ("affiliate", "year", "month")
        ordering = ["-year", "-month"]

    def __str__(self):
        return f"TierHistory {self.affiliate.slug} {self.year}/{self.month} vol={self.monthly_volume}"


# ─────────────────────────────────────────────────────────────────────────────
# Global Affiliate Budget Cap
# ─────────────────────────────────────────────────────────────────────────────

class AffiliateGlobalBudget(models.Model):
    """
    Singleton. Monthly global budget cap for affiliate rewards.
    Admin-controlled.
    """
    monthly_cap = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("500000.00"), help_text="MXN")
    current_month_spend = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0.00"), help_text="MXN spent this month")
    cap_year = models.PositiveSmallIntegerField(default=2025)
    cap_month = models.PositiveSmallIntegerField(default=1)
    rewards_paused = models.BooleanField(default=False, help_text="Manually pause new reward generation")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Affiliate Global Budget"

    def __str__(self):
        return f"Budget Cap: {self.monthly_cap} MXN | Spent: {self.current_month_spend} | Paused: {self.rewards_paused}"

    @classmethod
    def get_singleton(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def is_cap_reached(self):
        return self.current_month_spend >= self.monthly_cap


# ─────────────────────────────────────────────────────────────────────────────
# Internal Admin Notes
# ─────────────────────────────────────────────────────────────────────────────

class AffiliateNote(BaseModel):
    """Internal staff note attached to an affiliate application or profile."""
    application = models.ForeignKey(AffiliateApplication, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    content = models.TextField()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Note on {self.application.email} by {self.author}"


# ─────────────────────────────────────────────────────────────────────────────
# Fraud Flags
# ─────────────────────────────────────────────────────────────────────────────

class AffiliateFraudFlag(BaseModel):
    """Tracks detected fraud signals."""

    class SignalType(models.TextChoices):
        SELF_REFERRAL = "self_referral", "Self Referral"
        SAME_IP = "same_ip", "Same IP Address"
        SAME_DEVICE = "same_device", "Same Device Fingerprint"
        SAME_GOVERNMENT_ID = "same_gov_id", "Same Government ID"
        SAME_BANK_ACCOUNT = "same_bank", "Same Bank Account (CLABE)"
        WASH_TRADING = "wash_trading", "Wash Trading"

    affiliate = models.ForeignKey(AffiliateProfile, on_delete=models.CASCADE, related_name="fraud_flags")
    attributed_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="fraud_flags_as_user")
    signal_type = models.CharField(max_length=20, choices=SignalType.choices)
    detail = models.TextField(blank=True)
    resolved = models.BooleanField(default=False)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="resolved_fraud_flags",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["affiliate", "resolved"]),
        ]

    def __str__(self):
        return f"FraudFlag: {self.signal_type} for {self.affiliate.slug} / user {self.attributed_user.email}"
