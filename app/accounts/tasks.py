from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def send_confirmation_email_task(self, email: str, full_name: str, otp: str):
    """Send account verification OTP email."""
    subject = "Payparo — Verify Your Email"
    message = (
        f"Hi {full_name},\n\n"
        f"Your verification code is: {otp}\n\n"
        f"This code expires in 5 minutes.\n\n"
        f"If you didn't create an account, please ignore this email.\n\n"
        f"— Payparo Team"
    )
    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [email],
            fail_silently=False,
        )
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def send_password_reset_email_task(self, email: str, full_name: str, otp: str):
    """Send password reset OTP email."""
    subject = "Payparo — Password Reset"
    message = (
        f"Hi {full_name},\n\n"
        f"Your password reset code is: {otp}\n\n"
        f"This code expires in 5 minutes.\n\n"
        f"If you didn't request this, please ignore this email.\n\n"
        f"— Payparo Team"
    )
    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [email],
            fail_silently=False,
        )
    except Exception as exc:
        raise self.retry(exc=exc)
