from django.urls import path
from . import consumers
from . import user_consumer

websocket_urlpatterns = [
    path('ws/chat/<uuid:conversation_id>/', consumers.ChatConsumer.as_asgi()),
    path('ws/user/', user_consumer.UserConsumer.as_asgi()),
]
