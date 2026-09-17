from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.test import APIClient
from app.accounts.social_auth import resolve_or_create_social_user

User = get_user_model()


class SocialAuthTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.google_url = reverse("google-login")
        self.apple_url = reverse("apple-login")

    # ------------------
    # User Resolution Tests
    # ------------------
    def test_create_new_social_user(self):
        user = resolve_or_create_social_user(
            provider=User.AuthProvider.GOOGLE,
            provider_uid="google_uid_123",
            email="testuser@example.com",
            full_name="Test User",
        )
        self.assertEqual(user.email, "testuser@example.com")
        self.assertEqual(user.full_name, "Test User")
        self.assertEqual(user.provider_uid, "google_uid_123")
        self.assertEqual(user.auth_provider, User.AuthProvider.GOOGLE)
        self.assertTrue(user.is_active)
        self.assertTrue(user.username.startswith("testuser"))

    def test_link_existing_email_user_to_social(self):
        # Existing email user (e.g. inactive pending OTP)
        existing = User.objects.create_user(
            email="existing@example.com",
            password="securepassword123",
            full_name="Original Name",
        )
        existing.is_active = False
        existing.save()

        # User now logs in via Apple
        user = resolve_or_create_social_user(
            provider=User.AuthProvider.APPLE,
            provider_uid="apple_sub_999",
            email="existing@example.com",
            full_name="Apple Provided Name",
        )
        self.assertEqual(user.id, existing.id)
        self.assertEqual(user.provider_uid, "apple_sub_999")
        self.assertTrue(user.is_active)  # Activated!

    def test_banned_user_raises_error(self):
        banned = User.objects.create_user(
            email="banned@example.com",
            password="pass",
            is_banned=True,
        )
        with self.assertRaises(ValidationError):
            resolve_or_create_social_user(
                provider=User.AuthProvider.GOOGLE,
                provider_uid="g_123",
                email="banned@example.com",
            )

    # ------------------
    # Google API Tests
    # ------------------
    @patch("app.accounts.views.verify_google_token")
    def test_google_login_success(self, mock_verify):
        mock_verify.return_value = {
            "sub": "google_sub_001",
            "email": "newgoogle@example.com",
            "name": "Google User",
            "picture": "https://example.com/photo.jpg",
        }

        response = self.client.post(
            self.google_url,
            {"id_token": "valid_mock_token"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["email"], "newgoogle@example.com")

        # Verify user in database
        db_user = User.objects.get(email="newgoogle@example.com")
        self.assertEqual(db_user.provider_uid, "google_sub_001")
        self.assertTrue(db_user.is_active)

    @patch("app.accounts.views.verify_google_token")
    def test_google_login_invalid_token(self, mock_verify):
        mock_verify.side_effect = ValidationError("Invalid or expired Google authentication token.")

        response = self.client.post(
            self.google_url,
            {"id_token": "expired_token"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)

    def test_google_login_missing_token(self):
        response = self.client.post(
            self.google_url,
            {},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # ------------------
    # Apple API Tests
    # ------------------
    @patch("app.accounts.views.verify_apple_token")
    def test_apple_login_success(self, mock_verify):
        mock_verify.return_value = {
            "sub": "apple_sub_002",
            "email": "appleuser@privaterelay.appleid.com",
        }

        response = self.client.post(
            self.apple_url,
            {
                "identity_token": "valid_apple_mock_token",
                "full_name": "Apple User",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["email"], "appleuser@privaterelay.appleid.com")

        # Verify user in database
        db_user = User.objects.get(email="appleuser@privaterelay.appleid.com")
        self.assertEqual(db_user.provider_uid, "apple_sub_002")
        self.assertEqual(db_user.full_name, "Apple User")
        self.assertEqual(db_user.auth_provider, User.AuthProvider.APPLE)

    @patch("app.accounts.views.verify_apple_token")
    def test_apple_login_invalid_token(self, mock_verify):
        mock_verify.side_effect = ValidationError("Invalid or expired Apple token.")

        response = self.client.post(
            self.apple_url,
            {"identity_token": "bad_token"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)

    @patch("app.accounts.tasks.send_2fa_email_task.delay")
    @patch("app.accounts.views.verify_google_token")
    def test_social_login_with_2fa_enabled(self, mock_verify, mock_email_task):
        user = User.objects.create_user(
            email="2fa_user@example.com",
            password="pass",
            two_factor_enabled=True,
        )
        mock_verify.return_value = {
            "sub": "google_sub_2fa",
            "email": "2fa_user@example.com",
            "name": "2FA User",
        }

        response = self.client.post(
            self.google_url,
            {"id_token": "valid_token"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data.get("requires_2fa"))
        self.assertIn("two_factor_token", response.data)
        mock_email_task.assert_called_once()

