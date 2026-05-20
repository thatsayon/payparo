from decimal import Decimal
from rest_framework import serializers
from cloudinary.utils import cloudinary_url

from .models import (
    AffiliateApplication,
    AffiliateProfile,
    AffiliateClick,
    AffiliateAttribution,
    AffiliateReward,
    AffiliateWithdrawal,
    AffiliateTierHistory,
    AffiliateGlobalBudget,
    AffiliateNote,
    AffiliateFraudFlag,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def cloudinary_url_safe(field):
    if not field:
        return None
    if hasattr(field, "url"):
        return field.url
    try:
        url, _ = cloudinary_url(str(field))
        return url
    except Exception:
        return None


# ─── Application ─────────────────────────────────────────────────────────────

class AffiliateApplicationSerializer(serializers.ModelSerializer):
    id_document_url = serializers.SerializerMethodField()
    tax_document_url = serializers.SerializerMethodField()
    bank_statement_url = serializers.SerializerMethodField()

    class Meta:
        model = AffiliateApplication
        fields = [
            "id", "full_name", "email", "phone",
            "platform", "community_name", "community_url",
            "community_member_count", "community_description",
            "desired_slug",
            "tax_id", "business_name", "country",
            "bank_name", "clabe", "account_holder_name",
            "id_document_url", "tax_document_url", "bank_statement_url",
            "status", "rejection_reason", "reviewed_at", "created_at",
        ]
        read_only_fields = ["id", "status", "rejection_reason", "reviewed_at", "created_at"]

    def get_id_document_url(self, obj):
        return cloudinary_url_safe(obj.id_document)

    def get_tax_document_url(self, obj):
        return cloudinary_url_safe(obj.tax_document)

    def get_bank_statement_url(self, obj):
        return cloudinary_url_safe(obj.bank_statement)


class AffiliateApplicationCreateSerializer(serializers.ModelSerializer):
    """Used by applicants to submit an affiliate application."""
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = AffiliateApplication
        fields = [
            "full_name", "email", "phone",
            "platform", "community_name", "community_url",
            "community_member_count", "community_description",
            "desired_slug",
            "tax_id", "business_name", "country",
            "bank_name", "clabe", "account_holder_name",
            "id_document", "tax_document", "bank_statement",
            "password",
        ]

    def validate_clabe(self, value):
        if value and len(value) != 18:
            raise serializers.ValidationError("CLABE must be exactly 18 digits.")
        return value

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        instance = super().create(validated_data)
        if password:
            from django.contrib.auth.hashers import make_password
            instance.password_hash = make_password(password)
            instance.save(update_fields=["password_hash"])
        return instance


# ─── Admin Application Detail ─────────────────────────────────────────────────

class AdminAffiliateApplicationSerializer(AffiliateApplicationSerializer):
    """Extended serializer for admin view — includes all fields."""
    notes = serializers.SerializerMethodField()
    user_email = serializers.SerializerMethodField()
    user_id = serializers.SerializerMethodField()

    class Meta(AffiliateApplicationSerializer.Meta):
        fields = AffiliateApplicationSerializer.Meta.fields + [
            "user_id", "user_email", "notes",
        ]

    def get_notes(self, obj):
        return AffiliateNoteSerializer(obj.notes.all(), many=True).data

    def get_user_email(self, obj):
        return obj.user.email if obj.user else None

    def get_user_id(self, obj):
        return str(obj.user.id) if obj.user else None


# ─── Admin Status Update ─────────────────────────────────────────────────────

class AffiliateStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=AffiliateApplication.Status.choices)
    rejection_reason = serializers.CharField(required=False, allow_blank=True)
    slug = serializers.CharField(
        required=False, allow_blank=True,
        help_text="Required when approving; sets the affiliate's vanity slug.",
    )


# ─── Profile ──────────────────────────────────────────────────────────────────

class AffiliateProfileSerializer(serializers.ModelSerializer):
    referral_url = serializers.SerializerMethodField()
    tier_display = serializers.CharField(source="get_tier_display", read_only=True)

    class Meta:
        model = AffiliateProfile
        fields = [
            "id", "affiliate_id", "slug", "referral_url",
            "tier", "tier_display",
            "total_earned", "total_pending_hold", "total_released",
            "total_paid_out", "withdrawable_balance",
            "is_active", "created_at",
        ]
        read_only_fields = fields

    def get_referral_url(self, obj):
        return obj.referral_url


# ─── Rewards ─────────────────────────────────────────────────────────────────

class AffiliateRewardSerializer(serializers.ModelSerializer):
    escrow_order_id = serializers.SerializerMethodField()
    referred_user_email = serializers.SerializerMethodField()

    class Meta:
        model = AffiliateReward
        fields = [
            "id", "reward_type", "state",
            "platform_fee", "commission_rate", "amount", "currency",
            "hold_until", "released_at", "voided_at", "void_reason",
            "escrow_order_id", "referred_user_email",
            "created_at",
        ]
        read_only_fields = fields

    def get_escrow_order_id(self, obj):
        return obj.escrow.order_id if obj.escrow else None

    def get_referred_user_email(self, obj):
        if obj.attribution and obj.attribution.referred_user:
            return obj.attribution.referred_user.email
        return None


# ─── Attribution / Referred Users ────────────────────────────────────────────

class AffiliateAttributionSerializer(serializers.ModelSerializer):
    referred_user_email = serializers.EmailField(source="referred_user.email", read_only=True)
    referred_user_full_name = serializers.CharField(source="referred_user.full_name", read_only=True)
    referred_user_id = serializers.UUIDField(source="referred_user.id", read_only=True)
    total_volume = serializers.SerializerMethodField()
    transaction_count = serializers.SerializerMethodField()
    total_commission_earned = serializers.SerializerMethodField()

    class Meta:
        model = AffiliateAttribution
        fields = [
            "id", "referred_user_id", "referred_user_email", "referred_user_full_name",
            "attributed_at", "first_transaction_discount_used", "activation_bonus_paid",
            "fraud_flagged", "total_volume", "transaction_count", "total_commission_earned",
        ]

    def get_total_volume(self, obj):
        from app.excrow.models import Escrow
        from django.db.models import Sum, Q
        result = Escrow.objects.filter(
            Q(created_by=obj.referred_user) | Q(receiver=obj.referred_user),
            status=Escrow.Status.COMPLETED,
        ).aggregate(total=Sum("price"))
        return result["total"] or Decimal("0.00")

    def get_transaction_count(self, obj):
        from app.excrow.models import Escrow
        from django.db.models import Q
        return Escrow.objects.filter(
            Q(created_by=obj.referred_user) | Q(receiver=obj.referred_user),
            status=Escrow.Status.COMPLETED,
        ).count()

    def get_total_commission_earned(self, obj):
        result = obj.rewards.filter(
            reward_type=AffiliateReward.RewardType.RECURRING_COMMISSION
        ).aggregate(total=__import__("django.db.models", fromlist=["Sum"]).Sum("amount"))
        return result["total"] or Decimal("0.00")


# ─── Withdrawal ───────────────────────────────────────────────────────────────

class AffiliateWithdrawalSerializer(serializers.ModelSerializer):
    cfdi_invoice_url = serializers.SerializerMethodField()

    class Meta:
        model = AffiliateWithdrawal
        fields = [
            "id", "amount", "currency",
            "bank_name", "clabe", "account_holder_name",
            "cfdi_invoice_url", "cfdi_invoice_number",
            "isr_withholding", "net_amount",
            "status", "rejection_reason", "transaction_ref",
            "reviewed_at", "created_at",
        ]
        read_only_fields = ["id", "currency", "isr_withholding", "net_amount", "status",
                            "rejection_reason", "transaction_ref", "reviewed_at", "created_at",
                            "cfdi_invoice_url"]

    def get_cfdi_invoice_url(self, obj):
        return cloudinary_url_safe(obj.cfdi_invoice)


class AffiliateWithdrawalCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = AffiliateWithdrawal
        fields = [
            "amount", "bank_name", "clabe", "account_holder_name",
            "cfdi_invoice", "cfdi_invoice_number",
        ]

    def validate_clabe(self, value):
        if len(value) != 18:
            raise serializers.ValidationError("CLABE must be exactly 18 digits.")
        return value

    def validate_amount(self, value):
        from .utils import PAYOUT_MINIMUM
        if value < PAYOUT_MINIMUM:
            raise serializers.ValidationError(f"Minimum payout is {PAYOUT_MINIMUM} MXN.")
        return value


# ─── Tier History ─────────────────────────────────────────────────────────────

class AffiliateTierHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = AffiliateTierHistory
        fields = ["id", "year", "month", "monthly_volume", "tier_applied", "commission_rate"]


# ─── Global Budget ────────────────────────────────────────────────────────────

class AffiliateGlobalBudgetSerializer(serializers.ModelSerializer):
    cap_remaining = serializers.SerializerMethodField()
    cap_utilisation_pct = serializers.SerializerMethodField()

    class Meta:
        model = AffiliateGlobalBudget
        fields = [
            "monthly_cap", "current_month_spend",
            "cap_year", "cap_month",
            "rewards_paused", "cap_remaining", "cap_utilisation_pct",
        ]

    def get_cap_remaining(self, obj):
        return max(Decimal("0.00"), obj.monthly_cap - obj.current_month_spend)

    def get_cap_utilisation_pct(self, obj):
        if obj.monthly_cap == 0:
            return 100
        return round(float(obj.current_month_spend / obj.monthly_cap * 100), 2)


class AffiliateGlobalBudgetUpdateSerializer(serializers.Serializer):
    monthly_cap = serializers.DecimalField(max_digits=16, decimal_places=2, required=False)
    rewards_paused = serializers.BooleanField(required=False)


# ─── Notes ────────────────────────────────────────────────────────────────────

class AffiliateNoteSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = AffiliateNote
        fields = ["id", "content", "author_name", "created_at"]
        read_only_fields = ["id", "author_name", "created_at"]

    def get_author_name(self, obj):
        return obj.author.full_name or obj.author.email if obj.author else "Unknown"


# ─── Fraud Flags ─────────────────────────────────────────────────────────────

class AffiliateFraudFlagSerializer(serializers.ModelSerializer):
    affiliate_slug = serializers.CharField(source="affiliate.slug", read_only=True)
    user_email = serializers.EmailField(source="attributed_user.email", read_only=True)

    class Meta:
        model = AffiliateFraudFlag
        fields = [
            "id", "affiliate_slug", "user_email", "signal_type", "detail",
            "resolved", "resolved_at", "created_at",
        ]
        read_only_fields = fields


# ─── Admin Withdrawal ─────────────────────────────────────────────────────────

class AdminAffiliateWithdrawalSerializer(AffiliateWithdrawalSerializer):
    affiliate_slug = serializers.CharField(source="affiliate.slug", read_only=True)
    affiliate_email = serializers.EmailField(source="affiliate.user.email", read_only=True)
    reviewed_by_name = serializers.SerializerMethodField()

    class Meta(AffiliateWithdrawalSerializer.Meta):
        fields = AffiliateWithdrawalSerializer.Meta.fields + [
            "affiliate_slug", "affiliate_email", "admin_notes", "reviewed_by_name",
        ]

    def get_reviewed_by_name(self, obj):
        if obj.reviewed_by:
            return obj.reviewed_by.full_name or obj.reviewed_by.email
        return None


class AdminWithdrawalStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["approved", "completed", "rejected"])
    rejection_reason = serializers.CharField(required=False, allow_blank=True)
    transaction_ref = serializers.CharField(required=False, allow_blank=True)
    admin_notes = serializers.CharField(required=False, allow_blank=True)
    isr_withholding = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
