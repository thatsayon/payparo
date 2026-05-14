from rest_framework import serializers
from app.excrow.models import Escrow

from app.accounts.models import UserAccount

class UserManagementUserSerializer(serializers.ModelSerializer):
    kyc_status = serializers.SerializerMethodField()
    kyc_label = serializers.SerializerMethodField()
    badge_class = serializers.SerializerMethodField()
    transaction_count = serializers.IntegerField(read_only=True)
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = UserAccount
        fields = ('id', 'full_name', 'email', 'kyc_status', 'kyc_label', 'badge_class', 'transaction_count')

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

class EscrowTransactionsSerializer(serializers.ModelSerializer):
    transaction = serializers.CharField(source='order_id')
    seller = serializers.SerializerMethodField()
    buyer = serializers.SerializerMethodField()
    items = serializers.CharField(source='product_name')
    escrow_amount = serializers.DecimalField(source='price', max_digits=12, decimal_places=2)

    class Meta:
        model = Escrow
        fields = (
            'id',
            'transaction',
            'seller',
            'buyer',
            'items',
            'escrow_amount',
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
    user_email = serializers.EmailField(source='user.email', read_only=True)
    user_full_name = serializers.CharField(source='user.full_name', read_only=True)

    class Meta:
        from app.accounts.models import KYCSubmission
        model = KYCSubmission
        fields = (
            'id',
            'user',
            'user_email',
            'user_full_name',
            'status',
            'rejection_reason',
            'submitted_at',
            'reviewed_at',
        )

class KYCStatusUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        from app.accounts.models import KYCSubmission
        model = KYCSubmission
        fields = ('status', 'rejection_reason')
        
    def update(self, instance, validated_data):
        from django.utils import timezone
        
        new_status = validated_data.get('status', instance.status)
        instance.status = new_status
        instance.rejection_reason = validated_data.get('rejection_reason', instance.rejection_reason)
        # Assuming we mark it as reviewed if it's approved or rejected
        if new_status in [instance.Status.APPROVED, instance.Status.REJECTED]:
            instance.reviewed_at = timezone.now()
        instance.save()
        return instance
