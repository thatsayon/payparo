from django.urls import path

from .views import (
    UserManagementView,
    EscrowTransactionsView,
    KYCSubmissionListView,
    KYCSubmissionStatusUpdateView,
    EscrowDetailPageView,
    AdminProfilePageView,
    AdminProfileUpdateView,
    AdminPasswordUpdateView,
    AdminWithdrawRequestListView,
    AdminWithdrawRequestStatusUpdateView,
)

urlpatterns = [
    path("users/", UserManagementView.as_view(), name="user-management"),
    path("escrows/", EscrowTransactionsView.as_view(), name="escrow-transactions"),
    path("escrows/<uuid:pk>/", EscrowDetailPageView.as_view(), name="escrow-detail"),
    path("kyc-requests/", KYCSubmissionListView.as_view(), name="kyc-list"),
    path("kyc-requests/<uuid:pk>/status/", KYCSubmissionStatusUpdateView.as_view(), name="kyc-status-update"),
    path("profile/", AdminProfilePageView.as_view(), name="admin-profile"),
    path("profile/update/", AdminProfileUpdateView.as_view(), name="admin-profile-update"),
    path("profile/password/", AdminPasswordUpdateView.as_view(), name="admin-password-update"),
    path("withdraw-requests/", AdminWithdrawRequestListView.as_view(), name="admin-withdraw-list"),
    path("withdraw-requests/<uuid:pk>/status/", AdminWithdrawRequestStatusUpdateView.as_view(), name="admin-withdraw-status-update"),
]