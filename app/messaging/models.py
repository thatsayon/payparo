from django.db import models
from django.contrib.auth import get_user_model
from cloudinary.models import CloudinaryField
from app.common.models import BaseModel

User = get_user_model()

class Conversation(BaseModel):
    participants = models.ManyToManyField(User, related_name='conversations')
    
    def __str__(self):
        return f"Conversation {self.id}"

class Message(BaseModel):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='messages_sent')
    body = models.TextField(blank=True, null=True)
    image = CloudinaryField(blank=True, null=True)
    reply_to = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='replies')
    is_read = models.BooleanField(default=False)
    
    class Meta:
        ordering = ['created_at']
        
    def __str__(self):
        return f"Message {self.id} by {self.sender.username}"

class Block(BaseModel):
    blocker = models.ForeignKey(User, on_delete=models.CASCADE, related_name='blocking')
    blocked = models.ForeignKey(User, on_delete=models.CASCADE, related_name='blocked_by')
    
    class Meta:
        unique_together = ('blocker', 'blocked')
        
    def __str__(self):
        return f"{self.blocker.username} blocked {self.blocked.username}"

class Report(BaseModel):
    reporter = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reports_made')
    reported = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reports_received')
    reason = models.TextField()
    
    def __str__(self):
        return f"Report by {self.reporter.username} against {self.reported.username}"
