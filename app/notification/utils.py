import logging
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from .models import Notification
from .serializers import NotificationSerializer

logger = logging.getLogger(__name__)

def send_notification(user, title, body, event_type, reference_id=None):
    """
    Fault-tolerant notification sender.
    Creates a DB record and pushes to the user's WebSocket channel.
    Any failure is caught and logged to prevent blocking the caller's execution.
    """
    try:
        # 1. Save to DB
        notif = Notification.objects.create(
            user=user,
            title=title,
            body=body,
            event_type=event_type,
            reference_id=reference_id
        )

        # 2. Serialize data for WS
        serializer = NotificationSerializer(notif)
        
        # 3. Push to WebSocket Group
        channel_layer = get_channel_layer()
        if channel_layer:
            async_to_sync(channel_layer.group_send)(
                f'user_{user.id}',
                {
                    'type': 'notification',
                    'data': serializer.data
                }
            )
            
    except Exception as e:
        # Log the error but do NOT raise it
        # This guarantees escrow flows and other features won't fail if redis or db drops for a second
        logger.error(f"Failed to send notification to {user.username}: {e}")
