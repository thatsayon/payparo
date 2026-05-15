from django.contrib import admin
from .models import (
    Conversation,
    Message,
    Block,
    Report,
)

admin.site.register(Conversation)
admin.site.register(Message)
admin.site.register(Block)
admin.site.register(Report)