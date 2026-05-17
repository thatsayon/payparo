"""
Gemini Vision client for dispute analysis.
Sends seller images, buyer evidence images, and dispute text to Gemini
and returns a structured JSON decision.
"""

import json
import logging
import urllib.request

import google.generativeai as genai
from django.conf import settings

logger = logging.getLogger("app")

SYSTEM_PROMPT = """
You are an AI-powered marketplace dispute verification system.

Your job is to analyze disputes between buyers and sellers using:
- seller product information
- seller product images
- buyer received product images/videos
- buyer dispute reason
- buyer notes

Your goal is NOT to make emotional judgments.

Your goal is to:
1. Compare the evidence objectively
2. Detect visible inconsistencies
3. Estimate confidence level
4. Decide whether:
   - buyer is likely correct
   - seller is likely correct
   - evidence is insufficient

You must behave conservatively.
If evidence is weak or unclear, request manual verification.

BUSINESS LOGIC:
1. Seller uploads: product title, description, condition, images
2. Buyer receives the product
3. Buyer opens a dispute: reason, notes, received product images
4. AI analyzes: seller claims, seller images, buyer evidence, visible mismatches,
   authenticity indicators, condition differences, missing items, packaging differences, damage evidence

IMPORTANT RULES:
- Never hallucinate details not visible in the evidence
- Never assume authenticity unless evidence strongly supports it
- Be conservative when uncertain
- Do not make legal conclusions
- Do not accuse users of fraud directly
- Focus only on visible or text-supported inconsistencies
- Ignore emotional language
- Use only the provided evidence

ANALYSIS REQUIREMENTS:

VISUAL ANALYSIS:
- logo differences, stitching differences, packaging mismatch
- product shape differences, visible material differences, color mismatch
- scratches/damage, missing accessories/items, signs of use
- visible counterfeit indicators

TEXT ANALYSIS:
- contradiction between seller description and buyer evidence
- mismatch between claimed condition and actual condition
- mismatch between claimed product and received product

QUALITY ANALYSIS:
- image clarity, insufficient evidence
- manipulated/edited-looking images
- screenshots instead of actual photos

CONFIDENCE RULES:
Confidence must be between 0.0 and 1.0.
- 0.90–1.00: very strong evidence
- 0.70–0.89: likely conclusion but not perfect certainty
- 0.50–0.69: uncertain / mixed evidence
- below 0.50: insufficient evidence

If confidence is below 0.65: manual_review must be true

DECISION TYPES (use ONLY these values):
- "buyer_likely_correct"
- "seller_likely_correct"
- "uncertain"

SPECIAL CASES:
If images are blurry, evidence is incomplete, important product angles are missing,
authenticity cannot be determined visually, or buyer evidence is too weak:
- set decision to "uncertain"
- lower confidence
- set manual_review=true

Return ONLY valid JSON with this schema:
{
  "decision": "buyer_likely_correct",
  "confidence": 0.91,
  "issues_detected": ["Logo mismatch", "Different stitching pattern"],
  "summary": "Buyer evidence shows noticeable differences from the seller listing images.",
  "manual_review": false
}

Be objective, conservative, and evidence-driven. Do not guess.
When uncertain, prefer manual verification.
""".strip()


def _download_image_bytes(url: str) -> bytes | None:
    """Download image from URL and return raw bytes, or None on failure."""
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return resp.read()
    except Exception as exc:
        logger.warning("Failed to download image %s: %s", url, exc)
        return None


def _fix_url(url: str) -> str:
    if not url:
        return url
    if url.startswith("http://"):
        return url.replace("http://", "https://", 1)
    if not url.startswith("http"):
        return "https://" + url
    return url


def analyze_dispute_with_gemini(
    *,
    product_name: str,
    product_description: str,
    dispute_reason: str,
    buyer_note: str,
    seller_image_urls: list[str],
    buyer_image_urls: list[str],
) -> dict:
    """
    Call Gemini with all dispute context and return the parsed decision dict.

    Returns a safe fallback dict on any error so the Celery task never crashes
    without writing something back to the database.
    """
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    if not api_key:
        logger.error("GEMINI_API_KEY is not configured.")
        return _fallback_response("GEMINI_API_KEY not configured.")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")

    # Build the user message text
    user_text = f"""
SELLER INFORMATION:
- Product Name: {product_name}
- Product Description: {product_description or "No description provided."}

BUYER DISPUTE:
- Reason: {dispute_reason}
- Buyer Notes: {buyer_note}

Now analyze the images provided (seller images first, then buyer evidence images).
Return ONLY valid JSON following the schema.
""".strip()

    # Build content parts: text + images
    parts = [user_text]

    seller_count = 0
    for url in seller_image_urls:
        data = _download_image_bytes(_fix_url(url))
        if data:
            parts.append({"inline_data": {"mime_type": _guess_mime(url), "data": data}})
            seller_count += 1

    buyer_count = 0
    for url in buyer_image_urls:
        data = _download_image_bytes(_fix_url(url))
        if data:
            parts.append({"inline_data": {"mime_type": _guess_mime(url), "data": data}})
            buyer_count += 1

    logger.info(
        "Gemini request: product=%s seller_images=%d buyer_images=%d",
        product_name, seller_count, buyer_count,
    )

    if seller_count == 0 and buyer_count == 0:
        return _fallback_response("No images could be loaded for analysis.")

    try:
        response = model.generate_content(
            [SYSTEM_PROMPT] + parts,
            generation_config=genai.GenerationConfig(
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )
        raw = response.text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        _validate_result(result)
        logger.info("Gemini decision: %s (confidence=%.2f)", result.get("decision"), result.get("confidence", 0))
        return result

    except json.JSONDecodeError as exc:
        logger.error("Gemini returned non-JSON: %s | error: %s", response.text[:200], exc)
        return _fallback_response("AI returned invalid JSON.")
    except Exception as exc:
        logger.error("Gemini API error: %s", exc)
        return _fallback_response(f"AI analysis failed: {exc}")


def _guess_mime(url: str) -> str:
    url_lower = url.lower()
    if ".png" in url_lower:
        return "image/png"
    if ".webp" in url_lower:
        return "image/webp"
    if ".gif" in url_lower:
        return "image/gif"
    return "image/jpeg"


def _validate_result(result: dict) -> None:
    allowed_decisions = {"buyer_likely_correct", "seller_likely_correct", "uncertain"}
    if result.get("decision") not in allowed_decisions:
        result["decision"] = "uncertain"
    conf = result.get("confidence", 0)
    if not isinstance(conf, (int, float)) or not 0.0 <= conf <= 1.0:
        result["confidence"] = 0.5
    if result.get("confidence", 0) < 0.65:
        result["manual_review"] = True
    if "manual_review" not in result:
        result["manual_review"] = False
    if "issues_detected" not in result:
        result["issues_detected"] = []
    if "summary" not in result:
        result["summary"] = ""


def _fallback_response(reason: str) -> dict:
    return {
        "decision": "uncertain",
        "confidence": 0.0,
        "issues_detected": [],
        "summary": reason,
        "manual_review": True,
    }
