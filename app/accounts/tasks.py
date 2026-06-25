from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags
import logging

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def send_confirmation_email_task(self, email: str, full_name: str, otp: str):
    """Send account verification OTP email."""
    subject = "Payparo — Verify Your Email"
    html_message = render_to_string('emails/action.html', {
        'title': subject,
        'full_name': full_name,
        'message_lines': ['Use the code below to verify your email address. This code expires in 5 minutes.'],
        'otp': otp
    })
    plain_message = strip_tags(html_message)
    try:
        send_mail(
            subject, plain_message, settings.DEFAULT_FROM_EMAIL, [email],
            html_message=html_message, fail_silently=True
        )
    except Exception as exc:
        logger.error(f"Failed to send confirmation email to {email}: {exc}")


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def send_password_reset_email_task(self, email: str, full_name: str, otp: str):
    """Send password reset OTP email."""
    subject = "Payparo — Password Reset"
    html_message = render_to_string('emails/action.html', {
        'title': subject,
        'full_name': full_name,
        'message_lines': ['Use the code below to reset your password. This code expires in 5 minutes.'],
        'otp': otp
    })
    plain_message = strip_tags(html_message)
    try:
        send_mail(
            subject, plain_message, settings.DEFAULT_FROM_EMAIL, [email],
            html_message=html_message, fail_silently=True
        )
    except Exception as exc:
        logger.error(f"Failed to send password reset email to {email}: {exc}")


@shared_task
def send_login_otp_task(email, name, otp):
    """Send login verification OTP email."""
    subject = "Login Verification Code"
    html_message = render_to_string('emails/action.html', {
        'title': subject,
        'full_name': name,
        'message_lines': ['Here is your login verification code:'],
        'otp': otp
    })
    plain_message = strip_tags(html_message)
    try:
        send_mail(
            subject, plain_message, settings.DEFAULT_FROM_EMAIL, [email],
            html_message=html_message, fail_silently=True
        )
    except Exception as exc:
        logger.error(f"Failed to send login OTP to {email}: {exc}")


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def send_2fa_email_task(self, email: str, full_name: str, otp: str):
    """Send a 2FA login OTP email."""
    subject = "Payparo — Login Verification Code"
    html_message = render_to_string('emails/action.html', {
        'title': subject,
        'full_name': full_name,
        'message_lines': [
            'Your two-factor authentication code is below. This code expires in 5 minutes.',
            'If you did not attempt to sign in, please change your password immediately.'
        ],
        'otp': otp
    })
    plain_message = strip_tags(html_message)
    try:
        send_mail(
            subject, plain_message, settings.DEFAULT_FROM_EMAIL, [email],
            html_message=html_message, fail_silently=True
        )
    except Exception as exc:
        logger.error(f"Failed to send 2FA email to {email}: {exc}")


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def send_kyc_invitation_email_task(self, email: str, invitation_link: str):
    """Send KYC specialist invitation email with a registration link."""
    subject = "Payparo — You're Invited to Join as a KYC Specialist"
    html_message = render_to_string('emails/action.html', {
        'title': subject,
        'message_lines': [
            'You have been invited to join Payparo as a KYC Specialist.',
            'Please click the button below to complete your registration.',
            'This invitation link is unique to you. Do not share it with others.'
        ],
        'button_text': 'Complete Registration',
        'button_url': invitation_link
    })
    plain_message = strip_tags(html_message)
    try:
        send_mail(
            subject, plain_message, settings.DEFAULT_FROM_EMAIL, [email],
            html_message=html_message, fail_silently=True
        )
    except Exception as exc:
        logger.error(f"Failed to send KYC invitation email to {email}: {exc}")


