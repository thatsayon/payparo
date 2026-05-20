from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from .models import ReferralProfile

@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_referral_profile(sender, instance, created, **kwargs):
    if created:
        ReferralProfile.objects.get_or_create(user=instance)

@receiver(post_save, sender='excrow.Escrow')
def link_delivered_escrow_to_referral(sender, instance, **kwargs):
    from app.excrow.models import Escrow
    if instance.status == Escrow.Status.DELIVERED:
        from .models import ReferralEarning
        parties = [instance.created_by, instance.receiver]
        parties = [p for p in parties if p is not None]
        
        pending_earnings = ReferralEarning.objects.filter(
            referred_user__in=parties,
            status=ReferralEarning.Status.PENDING,
            escrow__isnull=True
        )
        for earning in pending_earnings:
            earning.escrow = instance
            earning.save()
