from django.urls import path
from .views import ReferralDashboardView

urlpatterns = [
    path('dashboard/', ReferralDashboardView.as_view(), name='referral-dashboard'),
]
