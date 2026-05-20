from datetime import timedelta
from django.utils import timezone
from django.db import transaction
from celery import shared_task
from .models import ReferralProfile, ReferralEarning
from app.excrow.models import Escrow, EscrowStatusHistory
from app.profile.models import Wallet, WalletTransaction

def process_pending_referrals(referrer=None):
    """
    Scans pending ReferralEarning records, associates eligible escrows, and 
    automatically completes those that have been in DELIVERED state for at least 48 hours.
    Balances are credited directly to the referrer's wallet.
    """
    query = ReferralEarning.objects.filter(status=ReferralEarning.Status.PENDING)
    if referrer:
        query = query.filter(referrer=referrer)
        
    pending_earnings = query.select_related('referrer', 'referred_user', 'escrow')
    
    for earning in pending_earnings:
        # 1. If escrow is not yet associated, search for eligible escrows of the referred user
        if not earning.escrow:
            # Look for any escrow where the referred user was the creator or receiver 
            # and that has transitioned to DELIVERED, COMPLETED, or PAYMENT_RELEASED
            eligible_escrows = Escrow.objects.filter(
                referred_user_in_parties(earning.referred_user),
                status__in=[Escrow.Status.DELIVERED, Escrow.Status.COMPLETED, Escrow.Status.PAYMENT_RELEASED]
            ).order_by('created_at')
            
            if eligible_escrows.exists():
                earning.escrow = eligible_escrows.first()
                earning.save()
        
        # 2. If escrow is now associated, check if the 48-hour delivery period has passed
        if earning.escrow:
            escrow = earning.escrow
            if escrow.status in [Escrow.Status.DELIVERED, Escrow.Status.COMPLETED, Escrow.Status.PAYMENT_RELEASED]:
                # Find the status history record when the escrow was marked DELIVERED
                delivery_history = EscrowStatusHistory.objects.filter(
                    escrow=escrow,
                    status=Escrow.Status.DELIVERED
                ).order_by('created_at').first()
                
                if delivery_history:
                    delivery_time = delivery_history.created_at
                    # Check if 48 hours have passed since delivery
                    if timezone.now() >= delivery_time + timedelta(hours=48):
                        try:
                            approve_referral_earning(earning)
                        except Exception:
                            # Log exception if needed, continue processing others
                            pass

def referred_user_in_parties(user):
    from django.db.models import Q
    return Q(created_by=user) | Q(receiver=user)

def approve_referral_earning(earning):
    """
    Atomically updates earning status, credits referrer total earnings, 
    and deposits referral rewards into the referrer's wallet.
    """
    with transaction.atomic():
        # Re-fetch for update to avoid race conditions
        earning_to_update = ReferralEarning.objects.select_for_update().get(pk=earning.pk)
        if earning_to_update.status != ReferralEarning.Status.PENDING:
            return
            
        referrer = earning_to_update.referrer
        
        # 1. Complete the ReferralEarning status
        earning_to_update.status = ReferralEarning.Status.COMPLETED
        earning_to_update.save()
        
        # 2. Update referrer's ReferralProfile total earnings
        profile, _ = ReferralProfile.objects.select_for_update().get_or_create(user=referrer)
        profile.total_earnings = float(profile.total_earnings) + float(earning_to_update.amount)
        profile.save()
        
        # 3. Credit the referrer's Wallet & log transaction
        wallet, _ = Wallet.objects.select_for_update().get_or_create(user=referrer)
        wallet.balance = float(wallet.balance) + float(earning_to_update.amount)
        wallet.save()
        
        WalletTransaction.objects.create(
            wallet=wallet,
            transaction_type=WalletTransaction.TransactionType.DEPOSIT,
            amount=earning_to_update.amount,
            status=WalletTransaction.Status.COMPLETED,
            description=f"Referral commission for referring {earning_to_update.referred_user.email}"
        )

@shared_task
def check_and_approve_referrals():
    """
    Celery periodic task to automatically process pending referral earnings.
    """
    process_pending_referrals()
