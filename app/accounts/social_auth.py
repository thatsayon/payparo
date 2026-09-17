import logging
import requests
import jwt
from jwt import PyJWKClient, PyJWTError
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.contrib.auth import get_user_model

logger = logging.getLogger("app")
User = get_user_model()

# Cached JWK clients for public key verification
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
FIREBASE_JWKS_URL = "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"

_apple_jwks_client = None
_firebase_jwks_client = None


def _get_apple_jwks_client():
    global _apple_jwks_client
    if _apple_jwks_client is None:
        _apple_jwks_client = PyJWKClient(APPLE_JWKS_URL, cache_keys=True, max_cached_keys=16)
    return _apple_jwks_client


def _get_firebase_jwks_client():
    global _firebase_jwks_client
    if _firebase_jwks_client is None:
        _firebase_jwks_client = PyJWKClient(FIREBASE_JWKS_URL, cache_keys=True, max_cached_keys=16)
    return _firebase_jwks_client


def verify_google_token(id_token: str) -> dict:
    """
    Verify Google ID Token via Google's tokeninfo endpoint,
    with fallback verification for Firebase ID tokens.

    Returns dict with keys: 'sub', 'email', 'name', 'picture'
    """
    if not id_token or not isinstance(id_token, str):
        raise ValidationError("Google ID token is required.")

    # 1. Primary: Try Google tokeninfo API endpoint
    try:
        response = requests.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": id_token},
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()
            iss = data.get("iss", "")
            if iss not in ("accounts.google.com", "https://accounts.google.com"):
                raise ValidationError("Invalid Google token issuer.")

            allowed_audiences = getattr(settings, "GOOGLE_CLIENT_IDS", [])
            aud = data.get("aud", "")
            if allowed_audiences and aud not in allowed_audiences:
                # Also accept if audience matches project number prefix
                has_match = any(allowed in aud or aud in allowed for allowed in allowed_audiences)
                if not has_match:
                    logger.warning(f"Google token aud '{aud}' not in configured GOOGLE_CLIENT_IDS.")

            email = data.get("email")
            if not email:
                raise ValidationError("Google token does not contain an email address.")

            email_verified = data.get("email_verified")
            if email_verified not in (True, "true", "True", 1, "1"):
                raise ValidationError("Google email is not verified.")

            return {
                "sub": data.get("sub"),
                "email": email.lower().strip(),
                "name": data.get("name") or f"{data.get('given_name', '')} {data.get('family_name', '')}".strip(),
                "picture": data.get("picture", ""),
            }
    except requests.RequestException as e:
        logger.warning(f"Google tokeninfo request error: {e}")

    # 2. Fallback: Check if it is a Firebase Auth ID token
    try:
        jwks_client = _get_firebase_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)
        allowed_audiences = getattr(settings, "GOOGLE_CLIENT_IDS", [])
        project_ids = [aud for aud in allowed_audiences if "-" not in aud] or ["payparo-c78c0"]

        decoded = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        # Verify issuer matches Firebase
        iss = decoded.get("iss", "")
        if "securetoken.google.com" in iss:
            email = decoded.get("email")
            if not email:
                raise ValidationError("Firebase token does not contain an email address.")
            return {
                "sub": decoded.get("sub") or decoded.get("user_id"),
                "email": email.lower().strip(),
                "name": decoded.get("name", ""),
                "picture": decoded.get("picture", ""),
            }
    except (PyJWTError, Exception) as fb_err:
        logger.debug(f"Firebase token fallback error: {fb_err}")

    raise ValidationError("Invalid or expired Google authentication token.")


def verify_apple_token(identity_token: str) -> dict:
    """
    Verify Apple ID Token (identity_token) using Apple's JWKS public keys.

    Returns dict with keys: 'sub', 'email'
    """
    if not identity_token or not isinstance(identity_token, str):
        raise ValidationError("Apple identity token is required.")

    try:
        jwks_client = _get_apple_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(identity_token)

        allowed_audiences = getattr(settings, "APPLE_BUNDLE_IDS", ["com.payparo.app"])

        decoded = jwt.decode(
            identity_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=allowed_audiences,
            issuer="https://appleid.apple.com",
            options={"verify_exp": True},
        )

        sub = decoded.get("sub")
        if not sub:
            raise ValidationError("Apple token missing subject (sub).")

        email = decoded.get("email")
        if email:
            email = email.lower().strip()

        return {
            "sub": sub,
            "email": email,
        }
    except PyJWTError as e:
        logger.error(f"Apple token verification failed: {e}")
        raise ValidationError(f"Invalid or expired Apple token: {e}")
    except Exception as e:
        logger.error(f"Unexpected Apple verification error: {e}")
        raise ValidationError("Apple authentication failed.")


def resolve_or_create_social_user(
    provider: str,
    provider_uid: str,
    email: str,
    full_name: str = "",
    picture_url: str = None,
):
    """
    Find existing user or create a new user for social OAuth login.
    Links provider_uid to existing user accounts with matching email.
    """
    if not email:
        raise ValidationError("A valid email address is required for social login.")

    email = email.lower().strip()
    provider_uid = str(provider_uid) if provider_uid else None

    with transaction.atomic():
        user = None

        # 1. Match by provider_uid and provider
        if provider_uid:
            user = User.objects.filter(
                provider_uid=provider_uid,
                auth_provider=provider,
            ).first()

        # 2. Fallback match by email
        if not user:
            user = User.objects.filter(email=email).first()
            if user:
                # Link provider_uid if not set
                update_fields = []
                if not user.provider_uid and provider_uid:
                    # Ensure provider_uid is not taken by another user
                    if not User.objects.filter(provider_uid=provider_uid).exclude(id=user.id).exists():
                        user.provider_uid = provider_uid
                        update_fields.append("provider_uid")

                # If account was inactive (e.g. registered but unverified OTP),
                # social login proves email ownership -> activate
                if not user.is_active and not user.is_banned:
                    user.is_active = True
                    update_fields.append("is_active")

                if not user.full_name and full_name:
                    user.full_name = full_name[:80]
                    update_fields.append("full_name")

                if update_fields:
                    user.save(update_fields=update_fields)

        # 3. Create new user if not exists
        if not user:
            display_name = (full_name or email.split("@")[0]).strip()[:80]
            user = User.objects.create_user(
                email=email,
                full_name=display_name,
                auth_provider=provider,
                provider_uid=provider_uid,
                is_active=True,
            )

    if user.is_banned:
        raise ValidationError("Your account has been suspended.")

    return user
