from django.urls import path, include

from .views import (
    RegisterView,
    VerifyTokenView,
    VerifyOTPView,
    ResendRegistrationOTPView,
    LoginView,
    VerifyLogin2FAView,
    Resend2FALoginOTPView,
    LogoutView,
    ForgetPasswordView,
    ForgetPasswordOTPVerifyView,
    ForgotPasswordSetView,
    ResendForgetPasswordOTPView,
    RefreshAccessTokenView,
    UpdatePasswordView,
    Toggle2FAView,
    DeleteAccountView,
    KYCUploadIDCardView,
    KYCPublishView,
    KYCUploadFaceView,
)
from .admin_views import (
    InviteKYCView,
    ResendInviteView,
    ListKYCView,
    RemoveKYCView,
    AcceptInviteView,
    VerifyInviteTokenView,
    PendingKYCListView,
    KYCApprovalView,
)

urlpatterns = [
    # Registration
    path("register/", RegisterView.as_view(), name="register"),
    path("verify-token/", VerifyTokenView.as_view(), name="verify-token"),
    path("verify-otp/", VerifyOTPView.as_view(), name="verify-otp"),
    path("resend-otp/", ResendRegistrationOTPView.as_view(), name="resend-otp"),

    # Login / Logout
    path("login/", LoginView.as_view(), name="login"),
    path("login/2fa/verify/", VerifyLogin2FAView.as_view(), name="verify-login-2fa"),
    path("login/2fa/resend/", Resend2FALoginOTPView.as_view(), name="resend-login-2fa"),
    path("logout/", LogoutView.as_view(), name="logout"),

    # Forgot password
    path("forgot-password/", ForgetPasswordView.as_view(), name="forgot-password"),
    path("forgot-password/verify-otp/", ForgetPasswordOTPVerifyView.as_view(), name="forgot-password-verify-otp"),
    path("forgot-password/set/", ForgotPasswordSetView.as_view(), name="forgot-password-set"),
    path("forgot-password/resend-otp/", ResendForgetPasswordOTPView.as_view(), name="forgot-password-resend-otp"),

    # Token refresh
    path("refresh/", RefreshAccessTokenView.as_view(), name="refresh"),

    # Account management
    path("update-password/", UpdatePasswordView.as_view(), name="update-password"),
    path("settings/2fa/toggle/", Toggle2FAView.as_view(), name="toggle-2fa"),
    path("delete-account/", DeleteAccountView.as_view(), name="delete-account"),

    # KYC
    path("kyc/upload-id/", KYCUploadIDCardView.as_view(), name="kyc-upload-id"),
    path("kyc/publish/", KYCPublishView.as_view(), name="kyc-publish"),
    path("kyc/upload-face/", KYCUploadFaceView.as_view(), name="kyc-upload-face"),

    # Invitations
    path("accept-invite/", AcceptInviteView.as_view(), name="accept-invite"),
    path("verify-invite-token/", VerifyInviteTokenView.as_view(), name="verify-invite-token"),

    # Admin Management
    path("admin/kyc/invite/", InviteKYCView.as_view(), name="admin-kyc-invite"),
    path("admin/kyc/invite/<uuid:id>/resend/", ResendInviteView.as_view(), name="admin-kyc-resend"),
    path("admin/kyc/", ListKYCView.as_view(), name="admin-kyc-list"),
    path("admin/kyc/<uuid:id>/", RemoveKYCView.as_view(), name="admin-kyc-remove"),
    path("admin/kyc/pending/", PendingKYCListView.as_view(), name="admin-kyc-pending"),
    path("admin/kyc/<uuid:id>/review/", KYCApprovalView.as_view(), name="admin-kyc-review"),
]
