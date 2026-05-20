from decimal import Decimal
from django.utils import timezone
from django.db import transaction as db_transaction
from celery import shared_task

from .models import (
    AffiliateReward,
    AffiliateProfile,
    AffiliateTierHistory,
    AffiliateGlobalBudget,
    AffiliateAttribution,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Release held rewards after 14-day hold period
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(name="affiliate.release_held_rewards")
def release_held_affiliate_rewards():
    """
    Daily task: Moves AffiliateReward entries from pending_hold → released
    once hold_until has passed.
    Updates affiliate withdrawable_balance and total_released.
    """
    now = timezone.now()
    due = AffiliateReward.objects.select_for_update().filter(
        state=AffiliateReward.State.PENDING_HOLD,
        hold_until__lte=now,
    ).select_related("affiliate")

    with db_transaction.atomic():
        released_count = 0
        for reward in due:
            reward.state = AffiliateReward.State.RELEASED
            reward.released_at = now
            reward.save(update_fields=["state", "released_at"])

            profile = reward.affiliate
            profile.total_released = profile.total_released + reward.amount
            profile.total_pending_hold = max(Decimal("0.00"), profile.total_pending_hold - reward.amount)
            profile.withdrawable_balance = profile.withdrawable_balance + reward.amount
            profile.save(update_fields=["total_released", "total_pending_hold", "withdrawable_balance", "updated_at"])
            released_count += 1

    return f"Released {released_count} affiliate rewards."


# ─────────────────────────────────────────────────────────────────────────────
# 2. Void rewards for refunded / charged-back escrow
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(name="affiliate.void_rewards_for_escrow")
def void_rewards_for_refunded_escrow(escrow_id: str, reason: str = "Escrow refunded/charged back"):
    """
    Voids all pending_hold AffiliateReward entries tied to an escrow.
    Called when an escrow is refunded or chargeback occurs during hold period.
    After payout, deduction is handled separately via deduct_post_payout_chargeback.
    """
    with db_transaction.atomic():
        rewards = AffiliateReward.objects.select_for_update().filter(
            escrow_id=escrow_id,
            state=AffiliateReward.State.PENDING_HOLD,
        ).select_related("affiliate")

        now = timezone.now()
        for reward in rewards:
            reward.state = AffiliateReward.State.VOIDED
            reward.voided_at = now
            reward.void_reason = reason
            reward.save(update_fields=["state", "voided_at", "void_reason"])

            profile = reward.affiliate
            profile.total_pending_hold = max(Decimal("0.00"), profile.total_pending_hold - reward.amount)
            profile.save(update_fields=["total_pending_hold", "updated_at"])


# ─────────────────────────────────────────────────────────────────────────────
# 3. Post-payout chargeback deduction
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(name="affiliate.deduct_post_payout_chargeback")
def deduct_post_payout_chargeback(affiliate_id: str, amount: str, reason: str = "Post-payout chargeback"):
    """
    Creates a DEDUCTED reward entry (negative ledger), reducing withdrawable balance.
    Supports negative balances.
    """
    from decimal import Decimal as D
    deduction_amount = D(amount)

    with db_transaction.atomic():
        try:
            profile = AffiliateProfile.objects.select_for_update().get(pk=affiliate_id)
        except AffiliateProfile.DoesNotExist:
            return f"AffiliateProfile {affiliate_id} not found."

        AffiliateReward.objects.create(
            affiliate=profile,
            reward_type=AffiliateReward.RewardType.DEDUCTION,
            state=AffiliateReward.State.DEDUCTED,
            amount=-deduction_amount,
            platform_fee=Decimal("0.00"),
            void_reason=reason,
            idempotency_key=f"deduction:{affiliate_id}:{timezone.now().isoformat()}",
        )
        profile.withdrawable_balance -= deduction_amount
        profile.save(update_fields=["withdrawable_balance", "updated_at"])

    return f"Deducted {amount} MXN from affiliate {affiliate_id}."


# ─────────────────────────────────────────────────────────────────────────────
# 4. Monthly tier recalculation
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(name="affiliate.recalculate_tiers")
def recalculate_affiliate_tiers():
    """
    Runs on 1st of each month.
    Calculates last month's referred user transaction volume per affiliate.
    Upgrades tier to 40% if volume >= 100,000 MXN; resets to 30% otherwise.
    Saves history and updates AffiliateProfile.tier.
    """
    from app.excrow.models import Escrow
    from django.db.models import Sum, Q
    from .utils import TIER_VOLUME_THRESHOLD, TIER_RATES

    now = timezone.now()
    # Target last month
    if now.month == 1:
        target_year, target_month = now.year - 1, 12
    else:
        target_year, target_month = now.year, now.month - 1

    month_start = timezone.datetime(target_year, target_month, 1, tzinfo=timezone.utc)
    if target_month == 12:
        month_end = timezone.datetime(target_year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        month_end = timezone.datetime(target_year, target_month + 1, 1, tzinfo=timezone.utc)

    for profile in AffiliateProfile.objects.filter(is_active=True).iterator():
        # Get all referred users for this affiliate
        attributed_user_ids = AffiliateAttribution.objects.filter(
            affiliate=profile
        ).values_list("referred_user_id", flat=True)

        # Sum escrow transaction amounts where referred users were involved, in that month
        volume_result = Escrow.objects.filter(
            Q(created_by_id__in=attributed_user_ids) | Q(receiver_id__in=attributed_user_ids),
            status=Escrow.Status.COMPLETED,
            created_at__gte=month_start,
            created_at__lt=month_end,
        ).aggregate(total=Sum("price"))

        monthly_volume = volume_result["total"] or Decimal("0.00")

        new_tier = "elevated" if monthly_volume >= TIER_VOLUME_THRESHOLD else "base"
        commission_rate = TIER_RATES[new_tier]

        with db_transaction.atomic():
            AffiliateTierHistory.objects.update_or_create(
                affiliate=profile,
                year=target_year,
                month=target_month,
                defaults={
                    "monthly_volume": monthly_volume,
                    "tier_applied": new_tier,
                    "commission_rate": commission_rate,
                },
            )
            profile.tier = new_tier
            profile.save(update_fields=["tier", "updated_at"])

    return "Tier recalculation complete."


# ─────────────────────────────────────────────────────────────────────────────
# 5. Reset monthly budget cap tracker
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(name="affiliate.reset_monthly_budget")
def reset_monthly_budget():
    """Runs on 1st of each month. Resets current_month_spend and updates cap_year/month."""
    now = timezone.now()
    budget = AffiliateGlobalBudget.get_singleton()
    budget.current_month_spend = Decimal("0.00")
    budget.cap_year = now.year
    budget.cap_month = now.month
    budget.save()
    return f"Monthly budget reset for {now.year}/{now.month}."
