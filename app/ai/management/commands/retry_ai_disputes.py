from django.core.management.base import BaseCommand
from app.excrow.models import EscrowDispute
from app.ai.tasks import analyze_dispute

class Command(BaseCommand):
    help = "Send all pending_ai disputes to the Celery AI queue for processing"

    def add_arguments(self, parser):
        parser.add_argument(
            "--status",
            type=str,
            default="pending_ai",
            help="Status of disputes to process (default: pending_ai). E.g., pending_kyc"
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Process ALL disputes regardless of status (Use with caution!)"
        )

    def handle(self, *args, **options):
        status_filter = options["status"]
        process_all = options["all"]

        if process_all:
            disputes = EscrowDispute.objects.all()
            self.stdout.write(self.style.WARNING("Warning: Processing ALL disputes regardless of status."))
        else:
            disputes = EscrowDispute.objects.filter(status=status_filter)
            self.stdout.write(self.style.NOTICE(f"Processing disputes with status: '{status_filter}'"))

        count = disputes.count()
        if count == 0:
            self.stdout.write(self.style.SUCCESS("No disputes found matching criteria. Nothing to do."))
            return

        self.stdout.write(f"Found {count} disputes to process. Dispatching to Celery...")

        dispatched = 0
        for dispute in disputes:
            analyze_dispute.delay(str(dispute.id))
            dispatched += 1

        self.stdout.write(self.style.SUCCESS(f"Successfully dispatched {dispatched} disputes to the AI queue."))
