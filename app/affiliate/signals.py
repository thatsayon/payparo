"""
Signals that wire affiliate reward logic into the escrow lifecycle.

- When an escrow is COMPLETED → generate affiliate commissions
- When an escrow is REFUNDED → void pending_hold affiliate rewards
"""
from decimal import Decimal
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import transaction as db_transaction
from django.utils import timezone


@receiver(post_save, sender="excrow.Escrow")
def handle_escrow_status_change(sender, instance, created, **kwargs):
    """React to Escrow status changes for affiliate rewards."""
    from app.excrow.models import Escrow

    if created:
        return

    status = instance.status

    if status == Escrow.Status.COMPLETED:
        _generate_affiliate_rewards(instance)

    elif status in (Escrow.Status.REFUNDED,):
        from .tasks import void_rewards_for_refunded_escrow
        void_rewards_for_refunded_escrow.delay(str(instance.pk), reason="Escrow refunded")


def _generate_affiliate_rewards(escrow):
    """
    Called when escrow.status → COMPLETED.
    Finds attributions for both parties (created_by and receiver),
    generates recurring commission + first-transaction bonus if applicable.
    Idempotent: uses idempotency_key to prevent duplicate rewards.
    """
    from .models import (
        AffiliateAttribution,
        AffiliateReward,
        AffiliateProfile,
        AffiliateGlobalBudget,
    )
    from .utils import (
        calculate_platform_fee_mxn,
        calculate_affiliate_commission,
        get_tier_rate,
        ACTIVATION_BONUS_AMOUNT,
        FIRST_TRANSACTION_DISCOUNT_CAP,
    )

    # Check global budget cap
    budget = AffiliateGlobalBudget.get_singleton()
    if budget.rewards_paused or budget.is_cap_reached():
        return

    # Determine which users are referenced in this escrow
    participants = [u for u in [escrow.created_by, escrow.receiver] if u is not None]

    transaction_amount = escrow.price or Decimal("0.00")
    if transaction_amount == Decimal("0.00") and escrow.installments.exists():
        transaction_amount = sum(i.amount for i in escrow.installments.all())

    platform_fee = calculate_platform_fee_mxn(transaction_amount)

    with db_transaction.atomic():
        for user in participants:
            try:
                attribution = AffiliateAttribution.objects.select_related("affiliate").get(
                    referred_user=user,
                    fraud_flagged=False,
                )
            except AffiliateAttribution.DoesNotExist:
                continue

            affiliate = attribution.affiliate
            if not affiliate.is_active:
                continue

            rate = get_tier_rate(affiliate)

            # ── Recurring commission ──────────────────────────────────────────
            commission = calculate_affiliate_commission(platform_fee, rate)
            idempotency_key = f"{affiliate.pk}:recurring_commission:{escrow.pk}"

            if not AffiliateReward.objects.filter(idempotency_key=idempotency_key).exists():
                # Check if this is the first transaction for the referred user
                # Apply the first-transaction discount (cap 200 MXN)
                if not attribution.first_transaction_discount_used:
                    discount = min(platform_fee, FIRST_TRANSACTION_DISCOUNT_CAP)
                    attribution.first_transaction_discount_used = True
                    attribution.first_transaction_discount_amount = discount
                    attribution.save(update_fields=["first_transaction_discount_used", "first_transaction_discount_amount"])

                AffiliateReward.objects.create(
                    affiliate=affiliate,
                    attribution=attribution,
                    escrow=escrow,
                    reward_type=AffiliateReward.RewardType.RECURRING_COMMISSION,
                    state=AffiliateReward.State.PENDING_HOLD,
                    platform_fee=platform_fee,
                    commission_rate=rate,
                    amount=commission,
                    idempotency_key=idempotency_key,
                )

                # Update denormalised balance
                affiliate_profile = AffiliateProfile.objects.select_for_update().get(pk=affiliate.pk)
                affiliate_profile.total_earned += commission
                affiliate_profile.total_pending_hold += commission
                affiliate_profile.save(update_fields=["total_earned", "total_pending_hold", "updated_at"])

                # Update global budget spend
                budget_obj = AffiliateGlobalBudget.objects.select_for_update().get(pk=1)
                budget_obj.current_month_spend += commission
                budget_obj.save(update_fields=["current_month_spend"])

            # ── Activation bonus (one-time 200 MXN) ──────────────────────────
            if not attribution.activation_bonus_paid:
                bonus_key = f"{affiliate.pk}:activation_bonus:{user.pk}"
                if not AffiliateReward.objects.filter(idempotency_key=bonus_key).exists():
                    AffiliateReward.objects.create(
                        affiliate=affiliate,
                        attribution=attribution,
                        escrow=escrow,
                        reward_type=AffiliateReward.RewardType.ACTIVATION_BONUS,
                        state=AffiliateReward.State.PENDING_HOLD,
                        platform_fee=Decimal("0.00"),
                        commission_rate=Decimal("0.00"),
                        amount=ACTIVATION_BONUS_AMOUNT,
                        idempotency_key=bonus_key,
                    )
                    attribution.activation_bonus_paid = True
                    attribution.save(update_fields=["activation_bonus_paid"])

                    affiliate_profile = AffiliateProfile.objects.select_for_update().get(pk=affiliate.pk)
                    affiliate_profile.total_earned += ACTIVATION_BONUS_AMOUNT
                    affiliate_profile.total_pending_hold += ACTIVATION_BONUS_AMOUNT
                    affiliate_profile.save(update_fields=["total_earned", "total_pending_hold", "updated_at"])

                    budget_obj = AffiliateGlobalBudget.objects.select_for_update().get(pk=1)
                    budget_obj.current_month_spend += ACTIVATION_BONUS_AMOUNT
                    budget_obj.save(update_fields=["current_month_spend"])
