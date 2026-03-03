from rest_framework_simplejwt.tokens import RefreshToken


def get_tokens_for_user(user) -> dict:
    """Generate JWT access + refresh pair for the given user."""
    refresh = RefreshToken.for_user(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
    }
