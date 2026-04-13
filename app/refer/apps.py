from django.apps import AppConfig


class ReferConfig(AppConfig):
    name = 'app.refer'

    def ready(self):
        import app.refer.signals
