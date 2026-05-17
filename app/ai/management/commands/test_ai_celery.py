"""
Management command to verify Celery is working end-to-end.

Usage:
  python manage.py test_celery              # ping check only
  python manage.py test_celery --full       # ping + full AI task on a real dispute
  python manage.py test_celery --full --dispute-id <uuid>
  python manage.py test_celery --full --mock
"""

import time
import json

from django.core.management.base import BaseCommand, CommandError

# Import from tasks.py so the worker auto-discovers it
from app.ai.tasks import celery_ping as _celery_ping


class Command(BaseCommand):
    help = "Verify Celery is connected and tasks are processed correctly."

    def add_arguments(self, parser):
        parser.add_argument(
            "--full",
            action="store_true",
            help="After the ping test, also run the real analyze_dispute task.",
        )
        parser.add_argument(
            "--dispute-id",
            type=str,
            metavar="UUID",
            help="Real dispute ID to use for the --full test.",
        )
        parser.add_argument(
            "--mock",
            action="store_true",
            help="Use mock dispute data for the --full test (no DB needed).",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=30,
            help="Seconds to wait for a task result (default: 30).",
        )

    # ─────────────────────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        timeout    = options["timeout"]
        full       = options["full"]
        dispute_id = options.get("dispute_id")
        use_mock   = options.get("mock")

        self.stdout.write("\n" + "═" * 55)
        self.stdout.write("  Payparo — Celery Health Check")
        self.stdout.write("═" * 55 + "\n")

        # ── Step 1: Redis connectivity ────────────────────────────────────────
        self._check_redis()

        # ── Step 2: Ping round-trip ───────────────────────────────────────────
        self._ping_test(timeout)

        # ── Step 3 (optional): Full AI task ───────────────────────────────────
        if full:
            self.stdout.write("")
            if dispute_id:
                self._full_real(dispute_id, timeout)
            elif use_mock:
                self._full_mock(timeout)
            else:
                self.stdout.write(self.style.WARNING(
                    "  ⚠️  --full requires either --dispute-id <uuid> or --mock"
                ))

        self.stdout.write("\n" + "═" * 55 + "\n")

    # ── Redis check ───────────────────────────────────────────────────────────

    def _check_redis(self):
        self.stdout.write("  1️⃣   Redis / broker connectivity …", ending=" ")
        try:
            import redis
            from django.conf import settings
            broker_url = getattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/0")
            client = redis.from_url(broker_url, socket_connect_timeout=5)
            client.ping()
            self.stdout.write(self.style.SUCCESS("✅  Redis reachable"))
            self.stdout.write(f"       Broker : {broker_url}")
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"❌  FAILED"))
            raise CommandError(
                f"Cannot connect to Redis broker: {exc}\n"
                "Is Redis running? (docker compose up redis)"
            )

    # ── Ping task ─────────────────────────────────────────────────────────────

    def _ping_test(self, timeout: int):
        self.stdout.write("\n  2️⃣   Enqueue ping task …", ending=" ")
        try:
            payload = f"test-{int(time.time())}"
            result  = _celery_ping.apply_async(args=[payload])
            self.stdout.write(f"enqueued → task_id={result.id}")
        except Exception as exc:
            self.stdout.write(self.style.ERROR("❌  Could not enqueue task"))
            raise CommandError(f"Task enqueue failed: {exc}")

        self.stdout.write(f"       Waiting up to {timeout}s for worker …", ending=" ")
        self.stdout.flush()

        t0 = time.time()
        try:
            value = result.get(timeout=timeout)
            elapsed = time.time() - t0
            if value == f"pong:{payload}":
                self.stdout.write(self.style.SUCCESS(f"✅  Worker responded in {elapsed:.2f}s"))
                self.stdout.write(f"       Response: {value!r}")
            else:
                self.stdout.write(self.style.WARNING(f"⚠️   Unexpected response: {value!r}"))
        except Exception as exc:
            elapsed = time.time() - t0
            self.stdout.write(self.style.ERROR(f"❌  No response after {elapsed:.1f}s"))
            raise CommandError(
                f"Celery worker did not respond: {exc}\n"
                "Is the worker running? (docker compose up celery)"
            )

    # ── Full AI task — real dispute ───────────────────────────────────────────

    def _full_real(self, dispute_id: str, timeout: int):
        self.stdout.write("  3️⃣   Full analyze_dispute task (real DB record) …")
        from app.excrow.models import EscrowDispute

        try:
            dispute = EscrowDispute.objects.select_related("escrow").get(pk=dispute_id)
        except EscrowDispute.DoesNotExist:
            raise CommandError(f"EscrowDispute {dispute_id} not found.")

        self.stdout.write(f"       Product  : {dispute.escrow.product_name}")
        self.stdout.write(f"       Reason   : {dispute.reason}")
        self.stdout.write(f"       Status before: {dispute.status}")

        from app.ai.tasks import analyze_dispute
        task = analyze_dispute.apply_async(args=[str(dispute.id)])
        self.stdout.write(f"       Task ID  : {task.id}")
        self.stdout.write(f"       Waiting up to {timeout}s …", ending=" ")
        self.stdout.flush()

        t0 = time.time()
        try:
            task.get(timeout=timeout)
            elapsed = time.time() - t0
        except Exception as exc:
            elapsed = time.time() - t0
            self.stdout.write(self.style.ERROR(f"❌  Task failed after {elapsed:.1f}s: {exc}"))
            return

        # Reload from DB to see updated fields
        dispute.refresh_from_db()
        self.stdout.write(self.style.SUCCESS(f"✅  Completed in {elapsed:.2f}s"))
        self._print_dispute_result(dispute)

    # ── Full AI task — mock ───────────────────────────────────────────────────

    def _full_mock(self, timeout: int):
        """
        For mock mode we call the Gemini client directly (synchronous)
        rather than going through Celery, because there's no DB record.
        This still validates the full Gemini pipeline.
        """
        self.stdout.write("  3️⃣   Full AI pipeline test (mock data, synchronous) …")

        from app.ai.gemini_client import analyze_dispute_with_gemini
        from django.conf import settings

        api_key = getattr(settings, "GEMINI_API_KEY", "")
        if not api_key or api_key == "your_gemini_api_key_here":
            raise CommandError(
                "GEMINI_API_KEY is not set. Add it to .env:\n"
                "  GEMINI_API_KEY=<your_key>\n"
                "  https://aistudio.google.com/"
            )

        MOCK_SELLER = ["https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=800"]
        MOCK_BUYER  = ["https://images.unsplash.com/photo-1608231387042-66d1773070a5?w=800"]

        self.stdout.write("       Calling Gemini 1.5 Flash …", ending=" ")
        self.stdout.flush()

        t0 = time.time()
        try:
            result = analyze_dispute_with_gemini(
                product_name="Nike Air Max 90 – White",
                product_description="Brand new in box, authentic Nike Air Max 90, white/grey, size US10.",
                dispute_reason="Fake product",
                buyer_note="The swoosh is printed not stitched. Sole feels hollow. Box has no barcode.",
                seller_image_urls=MOCK_SELLER,
                buyer_image_urls=MOCK_BUYER,
            )
            elapsed = time.time() - t0
        except Exception as exc:
            elapsed = time.time() - t0
            self.stdout.write(self.style.ERROR(f"❌  Gemini call failed after {elapsed:.1f}s"))
            raise CommandError(str(exc))

        self.stdout.write(self.style.SUCCESS(f"✅  Gemini responded in {elapsed:.2f}s"))
        self._print_ai_result(result)

    # ── Pretty printers ───────────────────────────────────────────────────────

    def _print_dispute_result(self, dispute):
        self.stdout.write(f"\n       ┌─ Updated dispute record ─────────────────")
        self.stdout.write(f"       │  Status      : {dispute.status}")
        self.stdout.write(f"       │  AI decision : {dispute.ai_decision}")
        self.stdout.write(f"       │  Confidence  : {dispute.ai_confidence}")
        if dispute.ai_summary:
            self.stdout.write(f"       │  Summary     : {dispute.ai_summary[:100]}…")
        self.stdout.write(f"       └──────────────────────────────────────────")

    def _print_ai_result(self, result: dict):
        decision  = result.get("decision", "—")
        conf      = result.get("confidence", 0)
        manual    = result.get("manual_review", True)
        summary   = result.get("summary", "")
        issues    = result.get("issues_detected", [])

        bar = "[" + "█" * round(conf * 20) + "░" * (20 - round(conf * 20)) + "]"

        self.stdout.write(f"\n       ┌─ Gemini result ──────────────────────────")
        self.stdout.write(f"       │  Decision     : {decision}")
        self.stdout.write(f"       │  Confidence   : {conf:.0%}  {bar}")
        self.stdout.write(f"       │  Manual review: {'YES → pending_kyc' if manual else 'NO'}")
        if summary:
            self.stdout.write(f"       │  Summary      : {summary[:100]}")
        if issues:
            for issue in issues:
                self.stdout.write(f"       │    • {issue}")
        self.stdout.write(f"       └──────────────────────────────────────────")
        self.stdout.write(f"\n       Raw JSON:\n{json.dumps(result, indent=6)}")
