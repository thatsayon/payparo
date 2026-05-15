import json
import logging
from django.core.serializers.json import DjangoJSONEncoder
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from app.messaging.models import Conversation, Message, Block
from app.messaging.serializers import MessageSerializer

logger = logging.getLogger(__name__)

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.conversation_id = self.scope['url_route']['kwargs']['conversation_id']
        self.room_group_name = f'chat_{self.conversation_id}'
        self.user = self.scope['user']

        logger.info(f'🔌 WebSocket CONNECT attempt | user={self.user} | conv={self.conversation_id}')

        if self.user.is_anonymous:
            logger.warning(f'🚫 WebSocket rejected: anonymous user')
            await self.close(code=4401)
            return

        # Check if conversation exists and user is participant
        has_access = await self.check_conversation_access()
        if not has_access:
            logger.warning(f'🚫 WebSocket rejected: user {self.user} has no access to conv {self.conversation_id}')
            await self.close(code=4403)
            return

        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()
        logger.info(f'✅ WebSocket CONNECTED | user={self.user.username} | conv={self.conversation_id} | group={self.room_group_name}')

    async def disconnect(self, close_code):
        logger.info(f'🔌 WebSocket DISCONNECTED | user={getattr(self.user, "username", "?")} | conv={getattr(self, "conversation_id", "?")} | code={close_code}')
        # Leave room group
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )

    async def receive(self, text_data):
        data = json.loads(text_data)
        message_type = data.get('type', 'message')
        logger.info(f'📨 WebSocket RECEIVE | user={self.user.username} | type={message_type} | data={data}')

        if message_type == 'message':
            body = data.get('body')
            image_url = data.get('image_url')
            reply_to_id = data.get('reply_to_id')

            # Check for block
            is_blocked = await self.check_if_blocked()
            if is_blocked:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'You cannot send messages to this user.'
                }, cls=DjangoJSONEncoder))
                return

            if not body and not image_url:
                return

            # Save message
            message = await self.save_message(body, image_url, reply_to_id)
            if message is None:
                await self.send(text_data=json.dumps({'type': 'error', 'message': 'Failed to save message'}))
                return
            serialized_message = await self.get_serialized_message(message)

            logger.info(f'📤 Broadcasting message to group {self.room_group_name}: {serialized_message}')

            # Broadcast to conversation group (for active ChatView screens)
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'chat_message',
                    'message': serialized_message
                }
            )

            # Also broadcast to each participant's personal user group
            # so they receive messages even when not on the chat screen
            participant_ids = await self.get_participant_ids()
            for uid in participant_ids:
                if str(uid) != str(self.user.id):  # skip sender
                    await self.channel_layer.group_send(
                        f'user_{uid}',
                        {
                            'type': 'chat_message',
                            'message': serialized_message
                        }
                    )
            
        elif message_type == 'read_receipt':
            message_ids = data.get('message_ids', [])
            if message_ids:
                await self.mark_messages_read(message_ids)
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'read_receipt_broadcast',
                        'message_ids': message_ids,
                        'reader_id': self.user.id
                    }
                )

    async def chat_message(self, event):
        message = event['message']
        await self.send(text_data=json.dumps({
            'type': 'chat_message',
            'message': message
        }, cls=DjangoJSONEncoder))

    async def read_receipt_broadcast(self, event):
        await self.send(text_data=json.dumps({
            'type': 'read_receipt',
            'message_ids': event['message_ids'],
            'reader_id': event['reader_id']
        }, cls=DjangoJSONEncoder))

    @database_sync_to_async
    def check_conversation_access(self):
        try:
            conversation = Conversation.objects.get(id=self.conversation_id)
            return conversation.participants.filter(id=self.user.id).exists()
        except Conversation.DoesNotExist:
            return False

    @database_sync_to_async
    def check_if_blocked(self):
        try:
            conversation = Conversation.objects.get(id=self.conversation_id)
            other_user = conversation.participants.exclude(id=self.user.id).first()
            if not other_user:
                return False
            # Return true if other user has blocked this user
            return Block.objects.filter(blocker=other_user, blocked=self.user).exists()
        except Exception:
            return False

    @database_sync_to_async
    def save_message(self, body, image_url, reply_to_id):
        try:
            message = Message.objects.create(
                conversation_id=self.conversation_id,
                sender=self.user,
                body=body or '',
                reply_to_id=reply_to_id
            )
            if image_url:
                message.image = image_url
                message.save()
            logger.info(f'✅ Message saved | id={message.id} | conv={self.conversation_id}')
            return message
        except Exception as e:
            logger.error(f'❌ Failed to save message: {e}')
            return None

    @database_sync_to_async
    def get_serialized_message(self, message):
        # Refresh from DB to get all related data
        message.refresh_from_db()
        data = MessageSerializer(message).data
        # Convert to plain dict with all UUID/Decimal fields as strings
        return json.loads(json.dumps(data, cls=DjangoJSONEncoder))

    @database_sync_to_async
    def mark_messages_read(self, message_ids):
        Message.objects.filter(
            id__in=message_ids, 
            conversation_id=self.conversation_id
        ).exclude(sender=self.user).update(is_read=True)

    @database_sync_to_async
    def get_participant_ids(self):
        try:
            conversation = Conversation.objects.get(id=self.conversation_id)
            return list(conversation.participants.values_list('id', flat=True))
        except Conversation.DoesNotExist:
            return []
