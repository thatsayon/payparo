import json
import logging
from django.core.serializers.json import DjangoJSONEncoder
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from app.messaging.models import Conversation, Message, Block
from app.messaging.serializers import MessageSerializer

User = get_user_model()

logger = logging.getLogger(__name__)


class UserConsumer(AsyncWebsocketConsumer):
    """
    Global per-user WebSocket consumer.
    Each authenticated user has one persistent connection at ws/user/.
    They join their personal group: user_<user_id>
    All messages and notifications addressed to this user are pushed here.
    """

    async def connect(self):
        self.user = self.scope['user']

        if self.user.is_anonymous:
            logger.warning('🚫 Global WS rejected: anonymous user')
            await self.close(code=4401)
            return

        self.user_group_name = f'user_{self.user.id}'

        await self.channel_layer.group_add(
            self.user_group_name,
            self.channel_name
        )
        await self.accept()
        logger.info(f'✅ Global WS CONNECTED | user={self.user.username} | group={self.user_group_name}')

    async def disconnect(self, close_code):
        logger.info(f'🔌 Global WS DISCONNECTED | user={getattr(self.user, "username", "?")} | code={close_code}')
        if hasattr(self, 'user_group_name'):
            await self.channel_layer.group_discard(
                self.user_group_name,
                self.channel_name
            )

    async def receive(self, text_data):
        data = json.loads(text_data)
        message_type = data.get('type', 'message')
        logger.info(f'📨 Global WS RECEIVE | user={self.user.username} | type={message_type} | data={data}')

        if message_type == 'message':
            conversation_id = data.get('conversation_id')
            body = data.get('body')
            image_url = data.get('image_url')
            reply_to_id = data.get('reply_to_id')

            if not conversation_id:
                logger.error("Missing conversation_id")
                await self.send(text_data=json.dumps({'type': 'error', 'message': 'Missing conversation_id'}))
                return

            # Check if user has access
            has_access = await self.check_conversation_access(conversation_id)
            if not has_access:
                logger.error("Access denied")
                await self.send(text_data=json.dumps({'type': 'error', 'message': 'Access denied'}))
                return

            # Check for block
            is_blocked = await self.check_if_blocked(conversation_id)
            if is_blocked:
                logger.error("User is blocked")
                await self.send(text_data=json.dumps({'type': 'error', 'message': 'You cannot send messages to this user.'}))
                return

            if not body and not image_url:
                logger.error("Empty message")
                return

            # Save message
            message = await self.save_message(conversation_id, body, image_url, reply_to_id)
            if message is None:
                logger.error("Message save returned None")
                await self.send(text_data=json.dumps({'type': 'error', 'message': 'Failed to save message'}))
                return
            
            logger.info(f"✅ Message saved! ID: {message.id}")
            serialized_message = await self.get_serialized_message(message)
            if serialized_message is None:
                await self.send(text_data=json.dumps({'type': 'error', 'message': 'Failed to serialize message'}))
                return
                
            logger.info(f"📤 Broadcasting message: {serialized_message}")

            # Broadcast to the conversation room (if any ChatViews are still listening there)
            room_group_name = f'chat_{conversation_id}'
            await self.channel_layer.group_send(
                room_group_name,
                {
                    'type': 'chat_message',
                    'message': serialized_message
                }
            )

            # Broadcast to all participants' global user groups
            participant_ids = await self.get_participant_ids(conversation_id)
            for uid in participant_ids:
                await self.channel_layer.group_send(
                    f'user_{uid}',
                    {
                        'type': 'chat_message',
                        'message': serialized_message
                    }
                )

        elif message_type == 'read_receipt':
            conversation_id = data.get('conversation_id')
            message_ids = data.get('message_ids', [])
            if conversation_id and message_ids:
                await self.mark_messages_read(conversation_id, message_ids)
                room_group_name = f'chat_{conversation_id}'
                
                # Broadcast to chat room
                await self.channel_layer.group_send(
                    room_group_name,
                    {
                        'type': 'read_receipt_broadcast',
                        'message_ids': message_ids,
                        'reader_id': str(self.user.id)
                    }
                )
                
                # Broadcast to participants' global user groups
                participant_ids = await self.get_participant_ids(conversation_id)
                for uid in participant_ids:
                    await self.channel_layer.group_send(
                        f'user_{uid}',
                        {
                            'type': 'read_receipt_broadcast',
                            'message_ids': message_ids,
                            'reader_id': str(self.user.id)
                        }
                    )

    # --- Event handlers (called by group_send from other consumers) ---

    async def chat_message(self, event):
        """Deliver a chat message to this user."""
        logger.info(f"📨 Delivering chat_message to user={self.user.username}")
        await self.send(text_data=json.dumps({
            'type': 'chat_message',
            'message': event['message'],
        }))

    async def notification(self, event):
        """Deliver a general notification to this user."""
        await self.send(text_data=json.dumps({
            'type': 'notification',
            'data': event['data'],
        }))

    async def read_receipt_broadcast(self, event):
        await self.send(text_data=json.dumps({
            'type': 'read_receipt',
            'message_ids': event['message_ids'],
            'reader_id': event['reader_id']
        }))

    # --- DB Helpers ---
    @database_sync_to_async
    def check_conversation_access(self, conversation_id):
        try:
            conversation = Conversation.objects.get(id=conversation_id)
            return conversation.participants.filter(id=self.user.id).exists()
        except Conversation.DoesNotExist:
            return False

    @database_sync_to_async
    def check_if_blocked(self, conversation_id):
        try:
            conversation = Conversation.objects.get(id=conversation_id)
            other_user = conversation.participants.exclude(id=self.user.id).first()
            if not other_user:
                return False
            return Block.objects.filter(blocker=other_user, blocked=self.user).exists()
        except Exception:
            return False

    @database_sync_to_async
    def save_message(self, conversation_id, body, image_url, reply_to_id):
        try:
            message = Message.objects.create(
                conversation_id=conversation_id,
                sender=self.user,
                body=body or '',
                reply_to_id=reply_to_id
            )
            if image_url:
                message.image = image_url
                message.save()
            return message
        except Exception as e:
            logger.error(f'❌ Global WS Failed to save message: {e}')
            return None

    @database_sync_to_async
    def get_serialized_message(self, message):
        try:
            message.refresh_from_db()
            data = MessageSerializer(message).data
            return json.loads(json.dumps(data, cls=DjangoJSONEncoder))
        except Exception as e:
            logger.error(f'❌ Global WS Failed to serialize message: {e}')
            return None

    @database_sync_to_async
    def mark_messages_read(self, conversation_id, message_ids):
        Message.objects.filter(
            id__in=message_ids, 
            conversation_id=conversation_id
        ).exclude(sender=self.user).update(is_read=True)

    @database_sync_to_async
    def get_participant_ids(self, conversation_id):
        try:
            conversation = Conversation.objects.get(id=conversation_id)
            return list(conversation.participants.values_list('id', flat=True))
        except Conversation.DoesNotExist:
            return []
