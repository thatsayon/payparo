from rest_framework import serializers

from app.accounts.models import KYCSubmission, UserAccount
from app.excrow.models import Escrow


class RevenueStatsSerializer(serializers.Serializer):
    today_revenue = serializers.DecimalField(max_digits=16, decimal_places=2)
    this_week_revenue = serializers.DecimalField(max_digits=16, decimal_places=2)
    this_month_revenue = serializers.DecimalField(max_digits=16, decimal_places=2)
    total_revenue = serializers.DecimalField(max_digits=16, decimal_places=2)
    total_escrow_volume = serializers.DecimalField(max_digits=16, decimal_places=2)
    total_completed_escrows = serializers.IntegerField()
    total_active_escrows = serializers.IntegerField()
    total_refunded_escrows = serializers.IntegerField()
    monthly_revenue = serializers.ListField(child=serializers.DictField())
    recent_escrows = serializers.ListField(child=serializers.DictField())


class UserManagementUserSerializer(serializers.ModelSerializer):
    kyc_status = serializers.SerializerMethodField()
    kyc_label = serializers.SerializerMethodField()
    badge_class = serializers.SerializerMethodField()
    transaction_count = serializers.IntegerField(read_only=True)
    full_name = serializers.SerializerMethodField()
    is_suspended = serializers.SerializerMethodField()

    class Meta:
        model = UserAccount
        fields = (
            "id",
            "full_name",
            "email",
            "kyc_status",
            "kyc_label",
            "badge_class",
            "transaction_count",
            "is_suspended",
            "date_joined",
            "role",
        )

    def get_full_name(self, obj):
        return obj.full_name or obj.username or obj.email

    def get_kyc_status(self, obj):
        return getattr(obj, "annotated_kyc_status", "not_submitted")

    def get_kyc_label(self, obj):
        status_value = self.get_kyc_status(obj)
        labels = {
            "pending": "Pending",
            "under_review": "Under Review",
            "approved": "Approved",
            "rejected": "Rejected",
            "not_submitted": "Not Submitted",
        }
        return labels.get(status_value, status_value.replace("_", " ").title())

    def get_badge_class(self, obj):
        status_value = self.get_kyc_status(obj)
        badges = {
            "pending": "pending",
            "under_review": "review",
            "approved": "approved",
            "rejected": "rejected",
            "not_submitted": "muted",
        }
        return badges.get(status_value, "muted")

    def get_is_suspended(self, obj):
        return not obj.is_active


class EscrowTransactionsSerializer(serializers.ModelSerializer):
    transaction = serializers.CharField(source="order_id")
    seller = serializers.SerializerMethodField()
    buyer = serializers.SerializerMethodField()
    items = serializers.CharField(source="product_name")
    escrow_amount = serializers.DecimalField(source="price", max_digits=12, decimal_places=2)

    class Meta:
        model = Escrow
        fields = (
            "id",
            "transaction",
            "seller",
            "buyer",
            "items",
            "escrow_amount",
        )

    def get_seller(self, obj):
        if obj.role == Escrow.Role.SELLER:
            user = obj.created_by
        else:
            user = obj.receiver

        if not user:
            return "N/A"
        return user.full_name or user.username or user.email

    def get_buyer(self, obj):
        if obj.role == Escrow.Role.BUYER:
            user = obj.created_by
        else:
            user = obj.receiver

        if not user:
            return "N/A"
        return user.full_name or user.username or user.email


class KYCSubmissionListSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_full_name = serializers.CharField(source="user.full_name", read_only=True)

    class Meta:
        model = KYCSubmission
        fields = (
            "id",
            "user",
            "user_email",
            "user_full_name",
            "status",
            "rejection_reason",
            "submitted_at",
            "reviewed_at",
        )


class KYCStatusUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = KYCSubmission
        fields = ("status", "rejection_reason")

    def update(self, instance, validated_data):
        from django.utils import timezone

        new_status = validated_data.get("status", instance.status)
        instance.status = new_status
        instance.rejection_reason = validated_data.get("rejection_reason", instance.rejection_reason)
        if new_status in [instance.Status.APPROVED, instance.Status.REJECTED]:
            instance.reviewed_at = timezone.now()
        instance.save()
        return instance


class EscrowPartySerializer(serializers.Serializer):
    label = serializers.CharField()
    name = serializers.CharField()
    email = serializers.EmailField(allow_null=True, required=False)
    role = serializers.CharField()


class EscrowTimelineItemSerializer(serializers.Serializer):
    label = serializers.CharField()
    status = serializers.CharField()
    timestamp = serializers.DateTimeField(allow_null=True)
    is_current = serializers.BooleanField()


class EscrowInspectionPeriodSerializer(serializers.Serializer):
    title = serializers.CharField()
    value = serializers.CharField()
    deadline = serializers.DateTimeField(allow_null=True)
    remaining_minutes = serializers.IntegerField(allow_null=True)
    is_active = serializers.BooleanField()


class EscrowFeeBreakdownSerializer(serializers.Serializer):
    transaction_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    platform_fee_label = serializers.CharField()
    platform_fee = serializers.DecimalField(max_digits=12, decimal_places=2)
    escrow_fee = serializers.DecimalField(max_digits=12, decimal_places=2)
    total = serializers.DecimalField(max_digits=12, decimal_places=2)


class EscrowAdminActionSerializer(serializers.Serializer):
    action = serializers.CharField()
    label = serializers.CharField()
    enabled = serializers.BooleanField()
    message = serializers.CharField(allow_null=True, allow_blank=True)


class EscrowDetailPageSerializer(serializers.Serializer):
    item_name = serializers.CharField()
    transaction_id = serializers.CharField()
    status = serializers.CharField()
    status_label = serializers.CharField()
    seller = EscrowPartySerializer()
    buyer = EscrowPartySerializer()
    timeline = EscrowTimelineItemSerializer(many=True)
    inspection_period = EscrowInspectionPeriodSerializer()
    fee_breakdown = EscrowFeeBreakdownSerializer()
    admin_actions = EscrowAdminActionSerializer(many=True)


class AdminProfilePhotoSerializer(serializers.Serializer):
    initials = serializers.CharField()
    url = serializers.URLField(allow_null=True, allow_blank=True)


class AdminProfileUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=80, required=False, allow_blank=False)
    email = serializers.EmailField(required=False)
    profile_pic = serializers.ImageField(required=False, allow_null=True)

    def update(self, instance, validated_data):
        if "name" in validated_data:
            instance.full_name = validated_data["name"]
        if "email" in validated_data:
            instance.email = validated_data["email"]
        if "profile_pic" in validated_data:
            instance.profile_pic = validated_data["profile_pic"]
        instance.save()
        return instance


class AdminPasswordUpdateSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, min_length=1)
    new_password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True, min_length=8)

    def validate(self, data):
        if data["new_password"] != data["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        return data


class AdminProfilePageSerializer(serializers.Serializer):
    profile_photo = AdminProfilePhotoSerializer()
    name = serializers.CharField()
    email = serializers.EmailField()


class AdminWithdrawRequestListSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_full_name = serializers.CharField(source="user.full_name", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        from app.profile.models import WithdrawTransaction
        model = WithdrawTransaction
        fields = (
            "id",
            "user",
            "user_email",
            "user_full_name",
            "method",
            "amount",
            "fee",
            "net_amount",
            "paypal_email",
            "bank_name",
            "account_number_last4",
            "transaction_ref",
            "status",
            "status_display",
            "description",
            "created_at",
        )
        read_only_fields = fields
