from django.urls import path
from app.excrow.views import (
    KYCDisputeAssignView,
    KYCDisputeResolveView,
)
from .views import (
    KYCAssignedDisputeListView,
    KYCAssignedDisputeDetailView,
    KYCDisputeListView,
)

urlpatterns = [
    path('disputes/assigned/<uuid:pk>/', KYCAssignedDisputeDetailView.as_view(), name='dispute-detail-assigned-kyc'),
    path('disputes/assigned/', KYCAssignedDisputeListView.as_view(), name='dispute-list-assigned-kyc'),
    path('disputes/unassigned/', KYCDisputeListView.as_view(), name='dispute-list-kyc'),
    path('disputes/<uuid:pk>/assign/', KYCDisputeAssignView.as_view(), name='dispute-assign-kyc'),
    path("disputes/<uuid:pk>/kyc-resolve/", KYCDisputeResolveView.as_view(), name="dispute-kyc-resolve"),
]
