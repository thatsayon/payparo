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


class UserAccount(AbstractBaseUser, PermissionsMixin, BaseModel):

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

    class TwoFactorMethod(models.TextChoices):
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"
        BOTH = "both", "Email + SMS"

    email = models.EmailField(
        _("email address"),
        unique=True,
    )

    full_name = models.CharField(max_length=80)
    username = models.CharField(max_length=50, unique=True, blank=True)

    profile_pic = CloudinaryField(blank=True, null=True)
    profile_updated_at = models.DateTimeField(blank=True, null=True)

    phone_number = models.CharField(max_length=20, blank=True, null=True)

    # 2FA SETTINGS
    two_factor_enabled = models.BooleanField(default=False)

    two_factor_method = models.CharField(
        max_length=10,
        choices=TwoFactorMethod.choices,
        blank=True,
        null=True
    )

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

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = CustomAccountManager()

    class Meta:
        indexes = [
            models.Index(fields=["username"]),
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
            
        # Automatically generate a unique username if none exists
        if not self.username and self.email:
            base_username = self.email.split("@")[0]
            # Strip invalid chars / keep it reasonable length
            base_username = "".join(c for c in base_username if c.isalnum() or c in "._-")[:40]
            if not base_username:
                base_username = "user"

            username = base_username
            counter = 1
            # Check uniqueness against other users (excluding self if updating)
            while UserAccount.objects.filter(username=username).exclude(id=self.id).exists():
                suffix = str(counter)
                # Ensure the combined length doesn't exceed max_length (50)
                username = f"{base_username[:50 - len(suffix)]}{suffix}"
                counter += 1
            
            self.username = username

        super().save(*args, **kwargs)

    @property
    def kyc_status(self):
        latest_submission = self.kyc_submissions.order_by('-submitted_at').first()
        if latest_submission:
            return latest_submission.status
        return "not_submitted"

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

class KYCSubmission(BaseModel):

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        UNDER_REVIEW = "under_review", "Under Review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    user = models.ForeignKey(
        UserAccount,
        on_delete=models.CASCADE,
        related_name="kyc_submissions"
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True
    )

    rejection_reason = models.TextField(blank=True)

    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

class KYCDocument(models.Model):

    class DocType(models.TextChoices):
        ID_FRONT = "id_front"
        ID_BACK = "id_back"
        FACE_FRONT = "face_front"
        FACE_LEFT = "face_left"
        FACE_RIGHT = "face_right"

    submission = models.ForeignKey(
        KYCSubmission,
        on_delete=models.CASCADE,
        related_name="documents"
    )

    document_type = models.CharField(
        max_length=20,
        choices=DocType.choices
    )

    image = CloudinaryField()

    uploaded_at = models.DateTimeField(auto_now_add=True)


class KYCIdentity(models.Model):

    submission = models.OneToOneField(
        KYCSubmission,
        on_delete=models.CASCADE,
        related_name="identity"
    )

    id_number = models.CharField(max_length=50)
    full_name = models.CharField(max_length=120)

    father_name = models.CharField(max_length=120)
    mother_name = models.CharField(max_length=120)

    date_of_birth = models.DateField()

    present_address = models.TextField()
    permanent_address = models.TextField()

    gender = models.CharField(max_length=10)
