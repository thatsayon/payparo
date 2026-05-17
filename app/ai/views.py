"""
Test API endpoint for the AI dispute analysis system.
Only accessible to staff/superusers.

POST /api/ai/test/
{
    "product_name": "Nike Air Max 90",
    "product_description": "Brand new, white colourway",
    "dispute_reason": "Fake product",
    "buyer_note": "Logo is printed not stitched",
    "seller_image_urls": ["https://..."],
    "buyer_image_urls":  ["https://..."]
}

OR pass a real dispute ID to load everything from the database:
{
    "dispute_id": "uuid-of-dispute"
}
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions, status
import time


class AIDisputeTestView(APIView):
    """
    Staff-only endpoint to test the Gemini AI dispute analysis.
    Accepts either a dispute_id (loads from DB) or manual fields.
    """
    permission_classes = [permissions.IsAdminUser]

    def post(self, request):
        data = request.data
        dispute_id = data.get("dispute_id", "").strip()

        if dispute_id:
            return self._analyze_real_dispute(dispute_id)
        return self._analyze_manual(data)

    # ── Real dispute ──────────────────────────────────────────────────────────

    def _analyze_real_dispute(self, dispute_id: str):
        from app.excrow.models import EscrowDispute, EscrowDisputeImage, EscrowImage

        try:
            dispute = EscrowDispute.objects.select_related("escrow").get(pk=dispute_id)
        except EscrowDispute.DoesNotExist:
            return Response({"success": False, "error": f"Dispute {dispute_id} not found."}, status=status.HTTP_404_NOT_FOUND)

        escrow = dispute.escrow
        seller_urls = [str(u) for u in EscrowImage.objects.filter(escrow=escrow).values_list("image", flat=True)]
        buyer_urls  = [str(u) for u in EscrowDisputeImage.objects.filter(dispute=dispute).values_list("image", flat=True)]

        return self._call_gemini(
            product_name=escrow.product_name or "",
            product_description=escrow.description or "",
            dispute_reason=dispute.reason,
            buyer_note=dispute.note,
            seller_image_urls=seller_urls,
            buyer_image_urls=buyer_urls,
            meta={
                "source": "database",
                "dispute_id": str(dispute.id),
                "escrow_id": str(escrow.id),
                "seller_images_count": len(seller_urls),
                "buyer_images_count": len(buyer_urls),
            },
        )

    # ── Manual fields ─────────────────────────────────────────────────────────

    def _analyze_manual(self, data: dict):
        return self._call_gemini(
            product_name=data.get("product_name", ""),
            product_description=data.get("product_description", ""),
            dispute_reason=data.get("dispute_reason", ""),
            buyer_note=data.get("buyer_note", ""),
            seller_image_urls=data.get("seller_image_urls", []),
            buyer_image_urls=data.get("buyer_image_urls", []),
            meta={"source": "manual"},
        )

    # ── Core ─────────────────────────────────────────────────────────────────

    def _call_gemini(self, *, product_name, product_description, dispute_reason,
                     buyer_note, seller_image_urls, buyer_image_urls, meta):
        from app.ai.gemini_client import analyze_dispute_with_gemini
        from django.conf import settings

        api_key = getattr(settings, "GEMINI_API_KEY", "")
        if not api_key or api_key == "your_gemini_api_key_here":
            return Response(
                {"success": False, "error": "GEMINI_API_KEY is not configured. Add it to your .env file."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        t0 = time.time()
        result = analyze_dispute_with_gemini(
            product_name=product_name,
            product_description=product_description,
            dispute_reason=dispute_reason,
            buyer_note=buyer_note,
            seller_image_urls=seller_image_urls,
            buyer_image_urls=buyer_image_urls,
        )
        elapsed = round(time.time() - t0, 2)

        # Predict what the Celery task would set as the new status
        decision   = result.get("decision", "uncertain")
        confidence = result.get("confidence", 0)
        manual     = result.get("manual_review", True)

        if manual or confidence < 0.65 or decision == "uncertain":
            predicted_status = "pending_kyc"
            predicted_label  = "KYC Resolver checking"
        elif decision == "buyer_likely_correct":
            predicted_status = "accepted"
            predicted_label  = "Dispute accepted — buyer wins"
        else:
            predicted_status = "declined"
            predicted_label  = "Dispute declined — seller wins"

        return Response({
            "success": True,
            "elapsed_seconds": elapsed,
            "meta": meta,
            "ai_result": result,
            "predicted_dispute_status": predicted_status,
            "predicted_dispute_label": predicted_label,
        }, status=status.HTTP_200_OK)
