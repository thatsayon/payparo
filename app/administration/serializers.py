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
