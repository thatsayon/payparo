from datetime import timedelta

from django.db import models
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.core.exceptions import ValidationError

from cloudinary.models import CloudinaryField

from app.common.models import BaseModel


class CustomAccountManager(BaseUserManager):

    def normalize_email_strict(self, email: str) -> str:
        return self.normalize_email(email).lower().strip()

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError(_("Email must be provided"))

        email = self.normalize_email_strict(email)

        user = self.model(email=email, **extra_fields)

        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()

        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if not extra_fields.get("is_staff"):
            raise ValueError("Superuser must have is_staff=True")

        if not extra_fields.get("is_superuser"):
            raise ValueError("Superuser must have is_superuser=True")

        return self.create_user(email, password, **extra_fields)


class UserAccount(AbstractBaseUser, PermissionsMixin):

    groups = models.ManyToManyField(
        'auth.Group',
        blank=True,
        related_name='account_set',
        related_query_name='account',
        verbose_name='groups',
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        blank=True,
        related_name='account_set',
        related_query_name='account',
        verbose_name='user permissions',
    )

    class AuthProvider(models.TextChoices):
        EMAIL = "email", "Email"
        GOOGLE = "google", "Google"

    email = models.EmailField(
        _("email address"),
        unique=True,
    )

    full_name = models.CharField(max_length=80)

    profile_pic = CloudinaryField(blank=True, null=True)
    profile_updated_at = models.DateTimeField(blank=True, null=True)

    auth_provider = models.CharField(
        max_length=20,
        choices=AuthProvider.choices,
        default=AuthProvider.EMAIL,
    )

    provider_uid = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        unique=True,
    )

    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_banned = models.BooleanField(default=False)

    date_joined = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = CustomAccountManager()

    class Meta:
        indexes = [
            models.Index(fields=["provider_uid"]),
            models.Index(fields=["date_joined"]),
        ]

    def clean(self):
        """
        Enforce auth consistency.
        """
        if self.auth_provider != self.AuthProvider.EMAIL and not self.provider_uid:
            raise ValidationError(
                "OAuth users must have provider_uid."
            )

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.lower().strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email


class OTP(BaseModel):
    user = models.ForeignKey(
        UserAccount,
        on_delete=models.CASCADE,
        related_name="otps",
    )

    otp_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["created_at"]),
        ]

    def is_valid(self, expiry_minutes=5):
        expiry_time = self.created_at + timedelta(minutes=expiry_minutes)
        return timezone.now() <= expiry_time

    def is_expired(self, expiry_minutes=5):
        return not self.is_valid(expiry_minutes)

    def __str__(self):
        return f"OTP for {self.user.email}"
