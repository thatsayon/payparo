from django.urls import path
from .views import ReferralDashboardView, ReferralCodeApplyView

urlpatterns = [
    path('dashboard/', ReferralDashboardView.as_view(), name='referral-dashboard'),
    path('apply/', ReferralCodeApplyView.as_view(), name='referral-apply'),
]
