import hashlib
import random
import string

import jwt
from datetime import datetime, timedelta, timezone
from django.conf import settings


def generate_otp(length: int = 6) -> str:
    """Generate a cryptographically random numeric OTP."""
    return "".join(random.choices(string.digits, k=length))


def hash_otp(otp: str) -> str:
    """Return SHA-256 hex digest of the OTP."""
    print(otp)
    return hashlib.sha256(otp.encode("utf-8")).hexdigest()


def create_otp_token(user_id, purpose: str = "verify") -> str:
    """
    Create a short-lived JWT used to tie an OTP flow to a user.

    Args:
        user_id: UUID (will be cast to str).
        purpose: 'verify' | 'reset' | 'reset_verified'
    """
    payload = {
        "user_id": str(user_id),
        "purpose": purpose,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def decode_otp_token(token: str) -> dict | None:
    """
    Decode and validate an OTP token.
    Returns payload dict on success, None on any failure.
    """
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None
