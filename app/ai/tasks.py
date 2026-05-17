"""
Celery task: analyze_dispute

Fires in the background after a new EscrowDispute is created.
Calls Gemini vision API with all available evidence and writes
the AI decision back to the EscrowDispute record.

Decision → Status mapping:
  buyer_likely_correct + confidence ≥ 0.65  →  accepted
  seller_likely_correct + confidence ≥ 0.65  →  declined
  uncertain OR confidence < 0.65             →  pending_kyc  (manual review)
"""

import json
import logging

from celery import shared_task
from django.db import transaction

logger = logging.getLogger("app")


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,       # retry after 60 s on transient errors
    name="ai.tasks.analyze_dispute",
)
def analyze_dispute(self, dispute_id: str):
    """
    Analyze an EscrowDispute with Gemini and update its status/fields.
    dispute_id is the UUID string primary key of EscrowDispute.
    """
    # Import here to avoid circular imports at module load
    from app.excrow.models import EscrowDispute, EscrowDisputeImage, EscrowImage
    from app.ai.gemini_client import analyze_dispute_with_gemini

    logger.info("analyze_dispute started for dispute_id=%s", dispute_id)

    try:
        dispute = EscrowDispute.objects.select_related("escrow").get(pk=dispute_id)
    except EscrowDispute.DoesNotExist:
        logger.error("EscrowDispute %s not found — aborting task.", dispute_id)
        return

    escrow = dispute.escrow

    # Collect seller images (from the escrow listing)
    seller_image_urls = list(
        EscrowImage.objects.filter(escrow=escrow)
        .values_list("image", flat=True)
    )

    # Collect buyer dispute images
    buyer_image_urls = list(
        EscrowDisputeImage.objects.filter(dispute=dispute)
        .values_list("image", flat=True)
    )

    # Convert CloudinaryField values to URL strings
    seller_image_urls = [str(u) for u in seller_image_urls if u]
    buyer_image_urls  = [str(u) for u in buyer_image_urls  if u]

    logger.info(
        "dispute=%s | seller_images=%d buyer_images=%d",
        dispute_id, len(seller_image_urls), len(buyer_image_urls),
    )

    try:
        result = analyze_dispute_with_gemini(
            product_name=escrow.product_name or "",
            product_description=escrow.description or "",
            dispute_reason=dispute.reason or "",
            buyer_note=dispute.note or "",
            seller_image_urls=seller_image_urls,
            buyer_image_urls=buyer_image_urls,
        )
    except Exception as exc:
        logger.error("Gemini call failed for dispute %s: %s", dispute_id, exc)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            # After all retries exhausted, escalate to human review
            result = {
                "decision": "uncertain",
                "confidence": 0.0,
                "issues_detected": [],
                "summary": f"AI analysis failed after retries: {exc}",
                "manual_review": True,
            }

    decision    = result.get("decision", "uncertain")
    confidence  = float(result.get("confidence", 0.0))
    summary     = result.get("summary", "")
    manual_review = result.get("manual_review", True)
    issues      = result.get("issues_detected", [])

    # Build a human-readable decision_reason
    issues_text = "\n".join(f"• {i}" for i in issues) if issues else ""
    decision_reason = summary
    if issues_text:
        decision_reason = f"{summary}\n\nIssues detected:\n{issues_text}"

    # ── Map AI decision → dispute status ──────────────────────────────────────
    #
    #  confidence ≥ 0.70 + buyer_likely_correct
    #      → awaiting_seller  (seller must accept or escalate)
    #  confidence ≥ 0.70 + seller_likely_correct
    #      → declined         (dispute rejected outright)
    #  anything else (uncertain / low confidence)
    #      → pending_kyc      (direct manual review)
    # ─────────────────────────────────────────────────────────────────────────

    from django.utils import timezone
    from datetime import timedelta

    if not manual_review and confidence >= 0.70 and decision == "buyer_likely_correct":
        new_status = EscrowDispute.StatusChoices.AWAITING_SELLER
        deadline   = timezone.now() + timedelta(hours=48)
    elif not manual_review and confidence >= 0.70 and decision == "seller_likely_correct":
        new_status = EscrowDispute.StatusChoices.DECLINED
        deadline   = None
    else:
        new_status = EscrowDispute.StatusChoices.PENDING_KYC
        deadline   = None

    with transaction.atomic():
        update_kwargs = dict(
            status=new_status,
            ai_decision=decision,
            ai_confidence=confidence,
            ai_summary=summary,
            decision_reason=decision_reason,
        )
        if deadline is not None:
            update_kwargs["seller_response_deadline"] = deadline
        EscrowDispute.objects.filter(pk=dispute_id).update(**update_kwargs)

    logger.info(
        "analyze_dispute done: dispute=%s decision=%s confidence=%.2f new_status=%s",
        dispute_id, decision, confidence, new_status,
    )


@shared_task(name="ai.tasks._celery_ping")
def celery_ping(payload: str = "ping") -> str:
    """Simple health-check task used by the test_ai_celery management command."""
    return f"pong:{payload}"
