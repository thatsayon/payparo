from django.apps import AppConfig


class AiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "app.ai"
    label = "ai"
    verbose_name = "AI Dispute Analysis"
