import json
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from app.messaging.models import Conversation, Message, Block
from channels.testing import WebsocketCommunicator
from config.asgi import application
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()

class MessagingRESTTests(TransactionTestCase):
    def setUp(self):
        self.client = APIClient()
        self.user1 = User.objects.create_user(email='user1@test.com', password='password123', full_name='User One', username='user1')
        self.user2 = User.objects.create_user(email='user2@test.com', password='password123', full_name='User Two', username='user2')
        self.token1 = str(RefreshToken.for_user(self.user1).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token1}')

    def test_create_conversation(self):
        url = reverse('conversation-list')
        response = self.client.post(url, {'username': 'user2'})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Conversation.objects.count(), 1)
        
    def test_block_user(self):
        url = reverse('block-user')
        response = self.client.post(url, {'blocked': self.user2.id})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Block.objects.count(), 1)

from channels.db import database_sync_to_async

@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class MessagingWSTests(TransactionTestCase):
    async def test_websocket_chat(self):
        # Create users
        user1 = await database_sync_to_async(User.objects.create_user)(email='ws1@test.com', password='password123', username='ws1')
        user2 = await database_sync_to_async(User.objects.create_user)(email='ws2@test.com', password='password123', username='ws2')
        
        # Create conversation
        conv = await Conversation.objects.acreate()
        await database_sync_to_async(conv.participants.add)(user1, user2)
        
        # Get token
        token1 = await database_sync_to_async(lambda: str(RefreshToken.for_user(user1).access_token))()
        
        # Connect communicator
        communicator = WebsocketCommunicator(application, f"/ws/chat/{conv.id}/?token={token1}")
        connected, subprotocol = await communicator.connect()
        self.assertTrue(connected)
        
        # Send message
        await communicator.send_json_to({
            "type": "message",
            "body": "Hello World",
        })
        
        # Receive broadcast
        response = await communicator.receive_json_from()
        self.assertEqual(response['type'], 'chat_message')
        self.assertEqual(response['message']['body'], 'Hello World')
        self.assertEqual(response['message']['sender_info']['id'], str(user1.id))
        
        await communicator.disconnect()
