from django.urls import path
from .views import AIDisputeTestView

urlpatterns = [
    path("test/", AIDisputeTestView.as_view(), name="ai-dispute-test"),
]
