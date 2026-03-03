from rest_framework import generics, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response

from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from django.db import transaction
from django.utils.timezone import now
from django.contrib.auth import authenticate, get_user_model

from .models import OTP
from .serializers import (
    RegisterSerializer,
    LoginSerializer,
    UpdatePasswordSerializer,
)
from .utils import generate_otp, hash_otp, create_otp_token, decode_otp_token
from .tokens import get_tokens_for_user

User = get_user_model()

MAX_ACTIVE_OTPS = 3  # Per-user cap to prevent abuse


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _first_error(serializer) -> str:
    """Extract the first human-readable error from serializer.errors."""
    for field, messages in serializer.errors.items():
        msg = str(messages[0]) if isinstance(messages, list) and messages else str(messages)
        if field == "non_field_errors":
            return msg
        return f"{field}: {msg}"
    return "Invalid data."


def _create_and_send_otp(user, send_task, purpose: str = "verify") -> str:
    """
    Generate OTP, hash-store it, prune old OTPs, dispatch email,
    and return a short-lived verification token.
    """
    otp = generate_otp()

    OTP.objects.create(user=user, otp_hash=hash_otp(otp))

    # Keep only the latest N OTPs per user
    otp_ids = (
        user.otps
        .order_by("-created_at")
        .values_list("id", flat=True)[MAX_ACTIVE_OTPS:]
    )
    if otp_ids:
        OTP.objects.filter(id__in=list(otp_ids)).delete()

    send_task.delay(user.email, user.full_name, otp)

    return create_otp_token(user.id, purpose=purpose)


def _verify_otp_for_user(user, raw_otp: str) -> bool:
    """
    Check raw_otp against stored hashes.
    Deletes expired OTPs as a side-effect.
    Returns True if a valid match is found (and deletes the match).
    """
    # Clean up expired first
    for otp_obj in user.otps.all():
        if otp_obj.is_expired():
            otp_obj.delete()

    hashed = hash_otp(raw_otp)
    otp_instance = user.otps.filter(otp_hash=hashed).first()

    if not otp_instance or not otp_instance.is_valid():
        return False

    otp_instance.delete()
    return True


# ──────────────────────────────────────────────
# Registration
# ──────────────────────────────────────────────

class RegisterView(generics.CreateAPIView):
    """
    Register a new user.
    Sets the account inactive until OTP verification.
    If an unverified account already exists for this email,
    resend the OTP instead of blocking.
    """
    permission_classes = [permissions.AllowAny]
    serializer_class = RegisterSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            # Handle unverified duplicate email
            email = request.data.get("email", "").lower().strip()
            if email and "email" in serializer.errors:
                try:
                    existing = User.objects.get(email=email)
                    if not existing.is_active:
                        # Resend OTP for unverified account
                        from .tasks import send_confirmation_email_task
                        token = _create_and_send_otp(
                            existing, send_confirmation_email_task, purpose="verify"
                        )
                        return Response(
                            {
                                "success": True,
                                "message": "Account exists but not verified. OTP resent.",
                                "user": {"id": str(existing.id), "email": existing.email},
                                "verificationToken": token,
                            },
                            status=status.HTTP_200_OK,
                        )
                except User.DoesNotExist:
                    pass

            return Response(
                {"error": _first_error(serializer)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                user = serializer.save()

                # User must verify email before login
                user.is_active = False
                user.save(update_fields=["is_active"])

                from .tasks import send_confirmation_email_task
                token = _create_and_send_otp(
                    user, send_confirmation_email_task, purpose="verify"
                )

            return Response(
                {
                    "success": True,
                    "message": "Registration successful. OTP sent to email.",
                    "user": {"id": str(user.id), "email": user.email},
                    "verificationToken": token,
                },
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            return Response(
                {"error": f"Registration failed: {e}"},
                status=status.HTTP_400_BAD_REQUEST,
            )


# ──────────────────────────────────────────────
# OTP Verification (Registration)
# ──────────────────────────────────────────────

class VerifyTokenView(APIView):
    """Check whether a verification or password-reset token is still valid."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        token = (
            request.data.get("verificationToken")
            or request.data.get("passResetToken")
            or request.data.get("passwordResetVerified")
        )
        if not token:
            return Response(
                {"error": "No token provided. Send 'verificationToken' or 'passResetToken'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        decoded = decode_otp_token(token)
        if not decoded:
            return Response(
                {"error": "Invalid or expired token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {"success": True, "message": "Token is valid.", "purpose": decoded.get("purpose")},
            status=status.HTTP_200_OK,
        )


class VerifyOTPView(APIView):
    """Verify registration OTP and activate the user account."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        otp = request.data.get("otp")
        token = request.data.get("verificationToken")

        if not otp or not token:
            return Response(
                {"error": "OTP and verification token are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        decoded = decode_otp_token(token)
        if not decoded or decoded.get("purpose") != "verify":
            return Response(
                {"error": "Invalid or expired token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(id=decoded["user_id"])
        except User.DoesNotExist:
            return Response(
                {"error": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not _verify_otp_for_user(user, otp):
            return Response(
                {"error": "Invalid or expired OTP."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.is_active = True
        user.save(update_fields=["is_active"])

        # Clean remaining OTPs for this user
        user.otps.all().delete()

        return Response(
            {"success": True, "message": "Account verified successfully."},
            status=status.HTTP_200_OK,
        )


class ResendRegistrationOTPView(APIView):
    """Resend the registration OTP for an unverified account."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        token = request.data.get("verificationToken")
        if not token:
            return Response(
                {"error": "No verification token provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        decoded = decode_otp_token(token)
        if not decoded or decoded.get("purpose") != "verify":
            return Response(
                {"error": "Invalid or expired token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(id=decoded["user_id"])
        except User.DoesNotExist:
            return Response(
                {"error": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if user.is_active:
            return Response(
                {"error": "Account is already verified."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from .tasks import send_confirmation_email_task
        new_token = _create_and_send_otp(
            user, send_confirmation_email_task, purpose="verify"
        )

        return Response(
            {
                "success": True,
                "message": "OTP resent to your email.",
                "verificationToken": new_token,
            },
            status=status.HTTP_200_OK,
        )


# ──────────────────────────────────────────────
# Login / Logout
# ──────────────────────────────────────────────

class LoginView(APIView):
    """Authenticate with email + password; returns JWT pair."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": _first_error(serializer)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = authenticate(
            username=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
        )
        if not user:
            return Response(
                {"error": "Invalid email or password."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {"error": "Account is not active. Please verify your email."},
                status=status.HTTP_403_FORBIDDEN,
            )

        tokens = get_tokens_for_user(user)

        return Response(
            {
                "success": True,
                "access": tokens["access"],
                "refresh": tokens["refresh"],
                "user": {
                    "id": str(user.id),
                    "email": user.email,
                    "full_name": user.full_name,
                },
            },
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    """Blacklist the refresh token to properly log out."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get("refresh")
        if refresh_token:
            try:
                token = RefreshToken(refresh_token)
                token.blacklist()
            except TokenError:
                pass  # Already expired / invalid — nothing to revoke

        return Response(
            {"success": True, "message": "Logged out successfully."},
            status=status.HTTP_200_OK,
        )


# ──────────────────────────────────────────────
# Forgot Password Flow
# ──────────────────────────────────────────────

class ForgetPasswordView(APIView):
    """Send a password-reset OTP to the user's email."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = request.data.get("email")
        if not email:
            return Response(
                {"error": "Email is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(email=email.lower().strip())
        except User.DoesNotExist:
            return Response(
                {"error": "No account found with this email."},
                status=status.HTTP_404_NOT_FOUND,
            )

        from .tasks import send_password_reset_email_task
        token = _create_and_send_otp(
            user, send_password_reset_email_task, purpose="reset"
        )

        return Response(
            {
                "success": True,
                "message": "Password reset OTP sent.",
                "user": {"id": str(user.id), "email": user.email},
                "passResetToken": token,
            },
            status=status.HTTP_200_OK,
        )


class ForgetPasswordOTPVerifyView(APIView):
    """Verify the password-reset OTP and return a verified token."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        otp = request.data.get("otp")
        reset_token = request.data.get("passResetToken")

        if not otp or not reset_token:
            return Response(
                {"error": "OTP and reset token are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        decoded = decode_otp_token(reset_token)
        if not decoded or decoded.get("purpose") != "reset":
            return Response(
                {"error": "Invalid or expired reset token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(id=decoded["user_id"])
        except User.DoesNotExist:
            return Response(
                {"error": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not _verify_otp_for_user(user, otp):
            return Response(
                {"error": "Invalid or expired OTP."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        verified_token = create_otp_token(user.id, purpose="reset_verified")

        return Response(
            {
                "success": True,
                "message": "OTP verified. You can now reset your password.",
                "passwordResetVerified": verified_token,
            },
            status=status.HTTP_200_OK,
        )


class ForgotPasswordSetView(APIView):
    """Set a new password using the verified reset token."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        new_password = request.data.get("new_password")
        verified_token = request.data.get("passwordResetVerified")

        if not new_password or not verified_token:
            return Response(
                {"error": "New password and verified token are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        decoded = decode_otp_token(verified_token)
        if not decoded or decoded.get("purpose") != "reset_verified":
            return Response(
                {"error": "Invalid or expired verified token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(id=decoded["user_id"])
        except User.DoesNotExist:
            return Response(
                {"error": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        user.set_password(new_password)
        user.save(update_fields=["password"])

        return Response(
            {"success": True, "message": "Password reset successfully."},
            status=status.HTTP_200_OK,
        )


class ResendForgetPasswordOTPView(APIView):
    """Resend the password-reset OTP."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        reset_token = request.data.get("passResetToken")
        if not reset_token:
            return Response(
                {"error": "No reset token provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        decoded = decode_otp_token(reset_token)
        if not decoded or decoded.get("purpose") != "reset":
            return Response(
                {"error": "Invalid or expired reset token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(id=decoded["user_id"])
        except User.DoesNotExist:
            return Response(
                {"error": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        from .tasks import send_password_reset_email_task
        new_token = _create_and_send_otp(
            user, send_password_reset_email_task, purpose="reset"
        )

        return Response(
            {
                "success": True,
                "message": "Password reset OTP resent.",
                "passResetToken": new_token,
            },
            status=status.HTTP_200_OK,
        )


# ──────────────────────────────────────────────
# Refresh Access Token
# ──────────────────────────────────────────────

class RefreshAccessTokenView(APIView):
    """Exchange a valid refresh token for a new access token."""
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return Response(
                {"error": "Refresh token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            refresh = RefreshToken(refresh_token)
            return Response(
                {"access": str(refresh.access_token)},
                status=status.HTTP_200_OK,
            )
        except TokenError as e:
            raise InvalidToken(e.args[0])


# ──────────────────────────────────────────────
# Account Management (Authenticated)
# ──────────────────────────────────────────────

class UpdatePasswordView(APIView):
    """Change password for the authenticated user."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = UpdatePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        if not user.check_password(serializer.validated_data["current_password"]):
            return Response(
                {"error": "Current password is incorrect."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])

        return Response(
            {"success": True, "message": "Password updated successfully."},
            status=status.HTTP_200_OK,
        )


class DeleteAccountView(APIView):
    """Permanently delete the authenticated user's account."""
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request):
        with transaction.atomic():
            request.user.otps.all().delete()
            request.user.delete()

        return Response(
            {"success": True, "message": "Account deleted permanently."},
            status=status.HTTP_200_OK,
        )
