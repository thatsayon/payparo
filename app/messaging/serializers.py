from rest_framework import serializers
from django.contrib.auth import get_user_model
from app.messaging.models import Conversation, Message, Block, Report
import cloudinary

User = get_user_model()

class UserSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'full_name', 'profile_pic']

class MessageSerializer(serializers.ModelSerializer):
    sender_info = UserSimpleSerializer(source='sender', read_only=True)
    reply_to_info = serializers.SerializerMethodField()
    image = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            'id', 'conversation', 'sender', 'sender_info', 'body', 
            'image', 'reply_to', 'reply_to_info', 'is_read', 
            'created_at', 'updated_at'
        ]
        read_only_fields = ['sender', 'is_read', 'created_at', 'updated_at']

    def get_image(self, obj):
        if not obj.image:
            return None
        try:
            # CloudinaryField stores the public_id; build a full URL from it
            return cloudinary.CloudinaryImage(str(obj.image)).build_url(secure=True)
        except Exception:
            return str(obj.image)  # fallback: return raw value

    def get_reply_to_info(self, obj):
        if obj.reply_to:
            # Also fix image URL in reply_to_info
            reply_image = None
            if obj.reply_to.image:
                try:
                    reply_image = cloudinary.CloudinaryImage(str(obj.reply_to.image)).build_url(secure=True)
                except Exception:
                    reply_image = str(obj.reply_to.image)
            return {
                'id': obj.reply_to.id,
                'body': obj.reply_to.body,
                'image': reply_image,
                'sender_id': obj.reply_to.sender_id,
            }
        return None


class ConversationSerializer(serializers.ModelSerializer):
    participants = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ['id', 'participants', 'title', 'is_dispute', 'created_at', 'updated_at', 'last_message', 'unread_count']

    def get_last_message(self, obj):
        last_msg = obj.messages.last()
        if last_msg:
            return MessageSerializer(last_msg).data
        return None

    def get_unread_count(self, obj):
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            return obj.messages.filter(is_read=False).exclude(sender=request.user).count()
        return 0

    def get_participants(self, obj):
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            other_participants = obj.participants.exclude(id=request.user.id)
            return UserSimpleSerializer(other_participants, many=True).data
        return UserSimpleSerializer(obj.participants.all(), many=True).data


class BlockSerializer(serializers.ModelSerializer):
    blocked_info = UserSimpleSerializer(source='blocked', read_only=True)

    class Meta:
        model = Block
        fields = ['id', 'blocker', 'blocked', 'blocked_info', 'created_at']
        read_only_fields = ['blocker', 'created_at']


class ReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = Report
        fields = ['id', 'reporter', 'reported', 'reason', 'created_at']
        read_only_fields = ['reporter', 'created_at']
