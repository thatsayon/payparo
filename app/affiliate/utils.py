from decimal import Decimal

from app.administration.models import FeeConfiguration


# ─── Platform Fee Calculation ─────────────────────────────────────────────────

PLATFORM_FEE_RATE_DEFAULT = Decimal("0.03")  # 3%
PLATFORM_FIXED_FEE_USD_DEFAULT = Decimal("1.00")  # 1 USD
USD_TO_MXN_RATE = Decimal("17.50")  # Approximate fallback; use live rate in production


def get_platform_fee_config():
    """Return (fee_rate, fixed_fee_usd) from FeeConfiguration or defaults."""
    config = FeeConfiguration.objects.first()
    if config:
        rate = (config.stripe_fee_percentage or Decimal("3.00")) / Decimal("100")
        fixed_usd = config.stripe_fixed_fee or PLATFORM_FIXED_FEE_USD_DEFAULT
    else:
        rate = PLATFORM_FEE_RATE_DEFAULT
        fixed_usd = PLATFORM_FIXED_FEE_USD_DEFAULT
    return rate, fixed_usd


def calculate_platform_fee_mxn(transaction_amount_mxn: Decimal) -> Decimal:
    """
    Compute the platform fee in MXN.
    Formula: 3% of transaction + 1 USD equivalent.
    """
    rate, fixed_usd = get_platform_fee_config()
    percentage_portion = (transaction_amount_mxn * rate).quantize(Decimal("0.01"))
    fixed_portion = (fixed_usd * USD_TO_MXN_RATE).quantize(Decimal("0.01"))
    return percentage_portion + fixed_portion


def calculate_affiliate_commission(platform_fee_mxn: Decimal, rate: Decimal) -> Decimal:
    """Affiliate earns `rate` fraction of the platform fee. Default 30%, elevated 40%."""
    return (platform_fee_mxn * rate).quantize(Decimal("0.01"))


# ─── Fraud Detection ──────────────────────────────────────────────────────────

def detect_fraud_signals(affiliate_profile, referred_user) -> list[str]:
    """
    Returns a list of fraud signal types detected between the affiliate and the referred user.
    Signals: self_referral, same_ip, same_device, same_gov_id, same_bank.
    """
    from app.affiliate.models import AffiliateClick, AffiliateAttribution

    signals = []

    # 1. Self-referral
    if affiliate_profile.user_id == referred_user.id:
        signals.append("self_referral")
        return signals  # No need to check further

    # 2. Same device fingerprint / IP from clicks
    user_attr = AffiliateAttribution.objects.filter(referred_user=referred_user).first()
    if user_attr and user_attr.click:
        user_click = user_attr.click
        # Check if any of the affiliate's own activity shares the same fingerprint or IP
        affiliate_clicks = AffiliateClick.objects.filter(affiliate=affiliate_profile)
        if user_click.device_fingerprint:
            if affiliate_clicks.filter(device_fingerprint=user_click.device_fingerprint).exclude(pk=user_click.pk).exists():
                signals.append("same_device")
        if user_click.ip_address:
            if affiliate_clicks.filter(ip_address=user_click.ip_address).exclude(pk=user_click.pk).exists():
                signals.append("same_ip")

    # 3. Same Government ID (KYC identity)
    try:
        from app.accounts.models import KYCSubmission, KYCIdentity
        affiliate_identity = KYCIdentity.objects.filter(submission__user=affiliate_profile.user).first()
        user_identity = KYCIdentity.objects.filter(submission__user=referred_user).first()
        if affiliate_identity and user_identity:
            if affiliate_identity.id_number and affiliate_identity.id_number == user_identity.id_number:
                signals.append("same_gov_id")
    except Exception:
        pass

    # 4. Same CLABE bank account
    try:
        affiliate_app = affiliate_profile.application
        if affiliate_app and affiliate_app.clabe:
            if hasattr(referred_user, "affiliate_application"):
                ref_app = referred_user.affiliate_application
                if ref_app and ref_app.clabe == affiliate_app.clabe:
                    signals.append("same_bank")
    except Exception:
        pass

    return signals


# ─── Tier Rate Helper ─────────────────────────────────────────────────────────

TIER_RATES = {
    "base": Decimal("0.30"),
    "elevated": Decimal("0.40"),
}
TIER_VOLUME_THRESHOLD = Decimal("100000.00")  # MXN per month
FIRST_TRANSACTION_DISCOUNT_CAP = Decimal("200.00")  # MXN
ACTIVATION_BONUS_AMOUNT = Decimal("200.00")  # MXN
PAYOUT_MINIMUM = Decimal("500.00")  # MXN


def get_tier_rate(affiliate_profile) -> Decimal:
    return TIER_RATES.get(affiliate_profile.tier, Decimal("0.30"))
