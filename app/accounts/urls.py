from django.urls import path

from .views import (
    RegisterView,
    VerifyTokenView,
    VerifyOTPView,
    ResendRegistrationOTPView,
    LoginView,
    LogoutView,
    ForgetPasswordView,
    ForgetPasswordOTPVerifyView,
    ForgotPasswordSetView,
    ResendForgetPasswordOTPView,
    RefreshAccessTokenView,
    UpdatePasswordView,
    DeleteAccountView,
    KYCUploadIDCardView,
    KYCPublishView,
    KYCUploadFaceView,
)

urlpatterns = [
    # Registration
    path("register/", RegisterView.as_view(), name="register"),
    path("verify-token/", VerifyTokenView.as_view(), name="verify-token"),
    path("verify-otp/", VerifyOTPView.as_view(), name="verify-otp"),
    path("resend-otp/", ResendRegistrationOTPView.as_view(), name="resend-otp"),

    # Login / Logout
    path("login/", LoginView.as_view(), name="login"),
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
    path("delete-account/", DeleteAccountView.as_view(), name="delete-account"),

    # KYC
    path("kyc/upload-id/", KYCUploadIDCardView.as_view(), name="kyc-upload-id"),
    path("kyc/publish/", KYCPublishView.as_view(), name="kyc-publish"),
    path("kyc/upload-face/", KYCUploadFaceView.as_view(), name="kyc-upload-face"),
]
