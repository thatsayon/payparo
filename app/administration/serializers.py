from rest_framework import serializers
from app.excrow.models import Escrow


class StatusOptionSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class UserManagementUserSerializer(serializers.Serializer):
    id = serializers.CharField()
    full_name = serializers.CharField()
    email = serializers.EmailField()
    kyc_status = serializers.CharField()
    kyc_label = serializers.CharField()
    badge_class = serializers.CharField()
    transaction_count = serializers.IntegerField()


class UserManagementPayloadSerializer(serializers.Serializer):
    page_title = serializers.CharField()
    subtitle = serializers.CharField()
    total_users = serializers.IntegerField()
    search_query = serializers.CharField()
    selected_status = serializers.CharField()
    status_options = StatusOptionSerializer(many=True)
    users = UserManagementUserSerializer(many=True)

class EscrowTransactionsSerializer(serializers.ModelSerializer):
    class Meta:
        model = Escrow
        fields = (
            'id',
            'order_id',
            'receiver',
            'product_name',
        )

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
