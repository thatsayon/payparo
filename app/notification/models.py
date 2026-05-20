from django.db import models
from django.conf import settings
from app.common.models import BaseModel

class Notification(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name="notifications"
    )
    title = models.CharField(max_length=255)
    body = models.TextField()
    event_type = models.CharField(max_length=100) # e.g. "escrow_created", "escrow_accepted", "escrow_delivered"
    is_read = models.BooleanField(default=False)
    reference_id = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} - {self.title} ({'Read' if self.is_read else 'Unread'})"
