from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Notification
from .serializers import NotificationSerializer

class NotificationListView(generics.ListAPIView):
    """
    Returns the authenticated user's notifications.
    Ordered by created_at descending.
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = NotificationSerializer

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)

class NotificationMarkReadView(APIView):
    """
    Marks a specific notification, or all notifications, as read.
    Send {"notification_id": "uuid"} or {"mark_all": True}
    """
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request):
        mark_all = request.data.get("mark_all", False)
        if mark_all:
            Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
            return Response({"success": True, "message": "All notifications marked as read."})

        notification_id = request.data.get("notification_id")
        if not notification_id:
            return Response({"error": "notification_id or mark_all is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            notif = Notification.objects.get(id=notification_id, user=request.user)
            notif.is_read = True
            notif.save()
            return Response({"success": True})
        except Notification.DoesNotExist:
            return Response({"error": "Notification not found."}, status=status.HTTP_404_NOT_FOUND)
