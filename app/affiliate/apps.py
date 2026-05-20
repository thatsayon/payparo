from django.apps import AppConfig


class AffiliateConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "app.affiliate"
    label = "affiliate"

    def ready(self):
        import app.affiliate.signals  # noqa: F401
