"""
Management command to test the AI dispute analysis system.

Usage examples:

  # Test with a real dispute by ID
  python manage.py test_ai_dispute --dispute-id <uuid>

  # Test with mock images (public URLs) and custom text
  python manage.py test_ai_dispute --mock

  # Test with custom seller/buyer image URLs
  python manage.py test_ai_dispute \\
      --product "Nike Air Max 90" \\
      --description "Brand new, white colourway, size US10" \\
      --reason "Fake product" \\
      --note "The logo looks printed not stitched, sole feels cheap" \\
      --seller-images "https://i.imgur.com/seller1.jpg" "https://i.imgur.com/seller2.jpg" \\
      --buyer-images  "https://i.imgur.com/buyer1.jpg"
"""

import json
import time

from django.core.management.base import BaseCommand, CommandError


MOCK_SELLER_IMAGES = [
    "https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=800",  # Nike shoe
]
MOCK_BUYER_IMAGES = [
    "https://images.unsplash.com/photo-1608231387042-66d1773070a5?w=800",  # Different shoe
]


class Command(BaseCommand):
    help = "Test the Gemini AI dispute analysis system."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            "--dispute-id",
            type=str,
            metavar="UUID",
            help="Run analysis against a real EscrowDispute record in the database.",
        )
        group.add_argument(
            "--mock",
            action="store_true",
            help="Use built-in mock data (no database needed).",
        )

        # Manual override args (used when --mock is not set and no --dispute-id)
        parser.add_argument("--product",      default="Test Product",              help="Product name")
        parser.add_argument("--description",  default="A premium quality product.", help="Product description")
        parser.add_argument("--reason",       default="Fake product",              help="Dispute reason")
        parser.add_argument("--note",         default="The item looks different from the listing.", help="Buyer note")
        parser.add_argument("--seller-images", nargs="+", metavar="URL", default=[], help="Seller image URLs")
        parser.add_argument("--buyer-images",  nargs="+", metavar="URL", default=[], help="Buyer image URLs")

        parser.add_argument(
            "--run-task",
            action="store_true",
            help="Also enqueue the real Celery task (requires Redis) and update the DB record.",
        )

    # ──────────────────────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        dispute_id = options.get("dispute_id")
        use_mock   = options.get("mock")
        run_task   = options.get("run_task")

        if dispute_id:
            self._test_real_dispute(dispute_id, run_task)
        elif use_mock:
            self._test_mock()
        else:
            self._test_custom(options)

    # ── Real dispute ──────────────────────────────────────────────────────────

    def _test_real_dispute(self, dispute_id: str, run_task: bool):
        from app.excrow.models import EscrowDispute, EscrowDisputeImage, EscrowImage

        self.stdout.write(f"\n🔍  Loading dispute {dispute_id} …\n")

        try:
            dispute = EscrowDispute.objects.select_related("escrow").get(pk=dispute_id)
        except EscrowDispute.DoesNotExist:
            raise CommandError(f"EscrowDispute with id={dispute_id} not found.")

        escrow = dispute.escrow
        seller_urls = [str(u) for u in EscrowImage.objects.filter(escrow=escrow).values_list("image", flat=True)]
        buyer_urls  = [str(u) for u in EscrowDisputeImage.objects.filter(dispute=dispute).values_list("image", flat=True)]

        self.stdout.write(f"   Product      : {escrow.product_name}")
        self.stdout.write(f"   Reason       : {dispute.reason}")
        self.stdout.write(f"   Note         : {dispute.note}")
        self.stdout.write(f"   Seller images: {len(seller_urls)}")
        self.stdout.write(f"   Buyer images : {len(buyer_urls)}\n")

        if run_task:
            self.stdout.write("⚙️   Enqueueing Celery task …")
            from app.ai.tasks import analyze_dispute
            result = analyze_dispute.delay(str(dispute.id))
            self.stdout.write(self.style.SUCCESS(f"✅  Task enqueued — ID: {result.id}"))
            self.stdout.write("    Check Celery worker logs for progress.\n")
        else:
            self._run_gemini_direct(
                product_name=escrow.product_name or "",
                description=escrow.description or "",
                reason=dispute.reason,
                note=dispute.note,
                seller_urls=seller_urls,
                buyer_urls=buyer_urls,
            )

    # ── Mock ─────────────────────────────────────────────────────────────────

    def _test_mock(self):
        self.stdout.write("\n🧪  Running with built-in mock data …\n")
        self.stdout.write(f"   Seller images: {MOCK_SELLER_IMAGES}")
        self.stdout.write(f"   Buyer images : {MOCK_BUYER_IMAGES}\n")
        self._run_gemini_direct(
            product_name="Nike Air Max 90 – White",
            description="Brand new in box, authentic Nike Air Max 90, white/grey colourway, size US10. Purchased from official Nike store.",
            reason="Fake product",
            note="The swoosh logo is printed not stitched. The sole material feels cheap and hollow. Box does not have the original barcode sticker.",
            seller_urls=MOCK_SELLER_IMAGES,
            buyer_urls=MOCK_BUYER_IMAGES,
        )

    # ── Custom args ───────────────────────────────────────────────────────────

    def _test_custom(self, options):
        seller_urls = options["seller_images"]
        buyer_urls  = options["buyer_images"]

        if not seller_urls and not buyer_urls:
            self.stdout.write(self.style.WARNING(
                "\n⚠️   No images provided. Running text-only analysis (lower quality).\n"
                "    Use --seller-images and --buyer-images to pass real evidence URLs.\n"
            ))

        self.stdout.write("\n🛠   Running with custom parameters …\n")
        self._run_gemini_direct(
            product_name=options["product"],
            description=options["description"],
            reason=options["reason"],
            note=options["note"],
            seller_urls=seller_urls,
            buyer_urls=buyer_urls,
        )

    # ── Core call ─────────────────────────────────────────────────────────────

    def _run_gemini_direct(
        self, *, product_name, description, reason, note, seller_urls, buyer_urls
    ):
        from app.ai.gemini_client import analyze_dispute_with_gemini
        from django.conf import settings

        api_key = getattr(settings, "GEMINI_API_KEY", "")
        if not api_key or api_key == "your_gemini_api_key_here":
            raise CommandError(
                "GEMINI_API_KEY is not configured.\n"
                "Set it in your .env file: GEMINI_API_KEY=<your_key>\n"
                "Get a free key at https://aistudio.google.com/"
            )

        self.stdout.write("🤖  Calling Gemini 1.5 Flash …")
        t0 = time.time()

        result = analyze_dispute_with_gemini(
            product_name=product_name,
            product_description=description,
            dispute_reason=reason,
            buyer_note=note,
            seller_image_urls=seller_urls,
            buyer_image_urls=buyer_urls,
        )

        elapsed = time.time() - t0

        # ── Pretty print result ──────────────────────────────────────────────
        self.stdout.write(f"\n{'─' * 55}")
        self.stdout.write(f"  ⏱  Completed in {elapsed:.1f}s")
        self.stdout.write(f"{'─' * 55}\n")

        decision  = result.get("decision", "—")
        conf      = result.get("confidence", 0)
        manual    = result.get("manual_review", True)
        summary   = result.get("summary", "")
        issues    = result.get("issues_detected", [])

        # Colour decision
        if decision == "buyer_likely_correct":
            decision_str = self.style.WARNING(f"  {decision}")
        elif decision == "seller_likely_correct":
            decision_str = self.style.SUCCESS(f"  {decision}")
        else:
            decision_str = self.style.ERROR(f"  {decision}")

        conf_bar = self._confidence_bar(conf)

        self.stdout.write(f"  Decision       : {decision_str}")
        self.stdout.write(f"  Confidence     : {conf:.0%}  {conf_bar}")
        self.stdout.write(f"  Manual review  : {'🔴 YES — escalate to KYC' if manual else '🟢 NO'}")
        self.stdout.write(f"\n  Summary:\n  {summary}\n")

        if issues:
            self.stdout.write("  Issues detected:")
            for issue in issues:
                self.stdout.write(f"    • {issue}")

        self.stdout.write(f"\n  Raw JSON:\n{json.dumps(result, indent=4)}\n")
        self.stdout.write(f"{'─' * 55}\n")

        # Status prediction
        if manual or conf < 0.65 or decision == "uncertain":
            predicted = "pending_kyc  →  KYC Resolver checking"
        elif decision == "buyer_likely_correct":
            predicted = "accepted  →  Dispute upheld (buyer wins)"
        else:
            predicted = "declined  →  Dispute rejected (seller wins)"

        self.stdout.write(f"  Predicted dispute status: {self.style.HTTP_INFO(predicted)}\n")

    @staticmethod
    def _confidence_bar(conf: float) -> str:
        filled = round(conf * 20)
        return "[" + "█" * filled + "░" * (20 - filled) + "]"
