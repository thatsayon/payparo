from celery import shared_task
from django.db import transaction
from app.excrow.models import Escrow, EscrowStatusHistory
from app.notification.utils import send_notification

@shared_task
def expire_unaccepted_buyer_escrow(escrow_id):
    try:
        escrow = Escrow.objects.select_related("created_by", "receiver").get(id=escrow_id)
    except Escrow.DoesNotExist:
        return
        
    if escrow.status == Escrow.Status.CREATED and escrow.role == Escrow.Role.BUYER:
        with transaction.atomic():
            escrow.refresh_from_db()
            if escrow.status != Escrow.Status.CREATED:
                return
            
            # Refund buyer (creator)
            wallet = escrow.created_by.wallet
            wallet.balance = float(wallet.balance) + float(escrow.total_amount)
            wallet.save(update_fields=["balance", "updated_at"])
            
            # Expire escrow
            escrow.status = Escrow.Status.CANCELLED
            escrow.save()
            
            EscrowStatusHistory.objects.create(escrow=escrow, status=Escrow.Status.CANCELLED)
            
            # Send notifications
            send_notification(
                user=escrow.created_by,
                title="Escrow Expired",
                body="The seller did not accept your escrow within 24 hours. The escrow was cancelled and your funds have been refunded.",
                event_type="escrow_expired",
                reference_id=str(escrow.id)
            )
            
            send_notification(
                user=escrow.receiver,
                title="Escrow Expired",
                body="You did not accept the escrow within 24 hours. The offer has expired.",
                event_type="escrow_expired",
                reference_id=str(escrow.id)
            )
