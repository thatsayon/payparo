from django.urls import path

from .views import (
    UserManagementView,
    EscrowTransactionsView,
    KYCSubmissionListView,
    KYCSubmissionStatusUpdateView,
)

urlpatterns = [
    path("users/", UserManagementView.as_view(), name="user-management"),
    path("escrows/", EscrowTransactionsView.as_view(), name="escrow-transactions"),
    path("kyc-requests/", KYCSubmissionListView.as_view(), name="kyc-list"),
    path("kyc-requests/<uuid:pk>/status/", KYCSubmissionStatusUpdateView.as_view(), name="kyc-status-update"),
]

