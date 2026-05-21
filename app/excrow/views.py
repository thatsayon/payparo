from rest_framework import generics, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser

from django.db import transaction
from django.db.models import Q
from django.contrib.auth import get_user_model

from .models import (
    Escrow, 
    EscrowImage, 
    EscrowDocument, 
    EscrowInstallment, 
    EscrowStatusHistory,
    EscrowDispute,
    EscrowDisputeImage,
    EscrowRating
)
from .serializers import (
    EscrowCreateSerializer,
    EscrowListSerializer,
    EscrowDetailSerializer,
    ReceiverSerializer,
    OrderHistorySerializer,
    OrderHistoryDetailSerializer,
    DisputeListSerializer,
    EscrowRatingSerializer,
    EscrowRatingReadSerializer,
)

User = get_user_model()


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _first_error(serializer) -> str:
    for field, messages in serializer.errors.items():
        msg = str(messages[0]) if isinstance(messages, list) and messages else str(messages)
        if field == "non_field_errors":
            return msg
        return f"{field}: {msg}"
    return "Invalid data."


# ──────────────────────────────────────────────
# Create + List Escrow
# ──────────────────────────────────────────────

from rest_framework.pagination import PageNumberPagination

class EscrowListCreateView(APIView):
    """
    GET  — List all escrows created by or received by the authenticated user.
    POST — Create a new escrow.

    Multipart form fields for POST:
      - receiver_username      (str, required)
      - role                   (str: seller | buyer, required)
      - item_type              (str: product | service, required)
      - product_name           (str, required)
      - description            (str, required)
      - payment_option         (str: single | installment, required)
      - price                  (decimal, required if payment_option=single)
      - currency               (str, optional, default USD)
      - installments           (list of decimals, required if payment_option=installment)
      - images                 (files, required, minimum 3)
      - documents              (files, optional, multiple)
    """
    permission_classes = [permissions.IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser]

    def get(self, request):
        escrows = Escrow.objects.filter(
            Q(created_by=request.user) | Q(receiver=request.user)
        ).select_related("created_by", "receiver").prefetch_related("images")

        paginator = PageNumberPagination()
        paginated_escrows = paginator.paginate_queryset(escrows, request, view=self)

        serializer = EscrowListSerializer(paginated_escrows, many=True)
        
        return Response({
            "success": True,
            "count": paginator.page.paginator.count,
            "next": paginator.get_next_link(),
            "previous": paginator.get_previous_link(),
            "results": serializer.data
        }, status=status.HTTP_200_OK)

    def post(self, request):
        print(request.data)
        data = request.data
        
        # 1. Required fields validation
        required_fields = ['role', 'item_type', 'product_name', 'description', 'payment_option', 'fee_amount']
        
        missing = []
        for field in required_fields:
            if not data.get(field):
                missing.append(field)
                
        payment_option = data.get('payment_option')
        if payment_option == Escrow.PaymentOption.SINGLE and not data.get('price'):
            missing.append('price')
                
        receiver_username = data.get('receiver_username') or data.get('receiver')
        if not receiver_username:
            missing.append('receiver')
            
        total_amount = data.get('total_amount') or data.get('total_amout')
        if not total_amount:
            missing.append('total_amout')
            
        if missing:
            return Response({"error": f"The following fields are required: {', '.join(missing)}"}, status=status.HTTP_400_BAD_REQUEST)
            
        # 2. Get receiver user
        receiver_username = receiver_username.strip()
        if receiver_username.startswith("@"):
            receiver_username = receiver_username[1:]
            
        try:
            receiver = User.objects.get(username=receiver_username, is_active=True)
        except User.DoesNotExist:
            return Response({"error": "No active user found with this username."}, status=status.HTTP_400_BAD_REQUEST)

        if receiver == request.user:
            return Response({"error": "You cannot create an escrow with yourself."}, status=status.HTTP_400_BAD_REQUEST)

        # 3. Create Escrow object
        from django.db import transaction
        
        def extract_list(field_name, source):
            if hasattr(source, "getlist"):
                val = source.getlist(field_name) or source.getlist(f"{field_name}[]")
            else:
                val = source.get(field_name) or source.get(f"{field_name}[]")
                if val and not isinstance(val, list):
                    val = [val]
            if not val:
                singular = field_name[:-1] if field_name.endswith('s') else field_name
                if hasattr(source, "getlist"):
                    val = source.getlist(singular)
                else:
                    s_val = source.get(singular)
                    val = [s_val] if s_val and not isinstance(s_val, list) else s_val
            if not val:
                keys = sorted([k for k in source.keys() if k.startswith(f"{field_name}[")])
                if keys:
                    val = [source[k] for k in keys]
            return val or []

        images = extract_list("images", request.FILES)
        documents = extract_list("documents", request.FILES)
        installments = extract_list("installments", request.data)
        
        if installments and len(installments) == 1 and isinstance(installments[0], str) and installments[0].startswith('['):
            import json
            try:
                installments = json.loads(installments[0])
            except ValueError:
                pass
                
        if data.get('item_type') == Escrow.ItemType.PRODUCT:
            if not images or len(images) < 3:
                return Response({"error": "At least 3 images are required for product type escrows."}, status=status.HTTP_400_BAD_REQUEST)
                
        if data.get('payment_option') == Escrow.PaymentOption.INSTALLMENT:
            if not installments:
                return Response({"error": "At least one installment amount is required for Custom Installments."}, status=status.HTTP_400_BAD_REQUEST)
                
        try:
            total_amount_val = float(total_amount)
        except (ValueError, TypeError):
            return Response({"error": "Invalid total amount."}, status=status.HTTP_400_BAD_REQUEST)

        if data.get('role') == Escrow.Role.BUYER:
            wallet = getattr(request.user, "wallet", None)
            if not wallet or float(wallet.balance) < total_amount_val:
                return Response({"error": "Insufficient wallet balance to fund this escrow. Please add funds to your wallet."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                if data.get('role') == Escrow.Role.BUYER:
                    wallet = request.user.wallet
                    wallet.balance = float(wallet.balance) - total_amount_val
                    wallet.save(update_fields=["balance", "updated_at"])

                escrow = Escrow.objects.create(
                    created_by=request.user,
                    receiver=receiver,
                    role=data.get('role'),
                    item_type=data.get('item_type'),
                    product_name=data.get('product_name'),
                    description=data.get('description'),
                    payment_option=data.get('payment_option'),
                    price=data.get('price'),
                    fee_amount=data.get('fee_amount'),
                    total_amount=total_amount,
                    currency=data.get('currency', 'USD')
                )
                
                if images:
                    EscrowImage.objects.bulk_create([
                        EscrowImage(escrow=escrow, image=img)
                        for img in images
                    ])
                    
                if documents:
                    EscrowDocument.objects.bulk_create([
                        EscrowDocument(escrow=escrow, file=doc)
                        for doc in documents
                    ])
                    
                if installments:
                    EscrowInstallment.objects.bulk_create([
                        EscrowInstallment(escrow=escrow, amount=amount, order=i + 1)
                        for i, amount in enumerate(installments)
                    ])
                    
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        from app.notification.utils import send_notification
        send_notification(
            user=receiver,
            title="Escrow Request",
            body=f"You have a new escrow request from {request.user.username}.",
            event_type="escrow_created",
            reference_id=str(escrow.id)
        )

        if escrow.role == Escrow.Role.BUYER:
            try:
                from app.excrow.tasks import expire_unaccepted_buyer_escrow
                expire_unaccepted_buyer_escrow.apply_async(args=[str(escrow.id)], countdown=86400)
            except Exception as exc:
                import logging
                logging.getLogger("app").warning("Could not enqueue expiration task for %s: %s", escrow.id, exc)

        return Response(
            {
                "success": True,
                "message": "Escrow created successfully.",
                "escrow":  EscrowDetailSerializer(escrow).data,
            },
            status=status.HTTP_201_CREATED,
        )


# ──────────────────────────────────────────────
# Retrieve Escrow Detail
# ──────────────────────────────────────────────

class EscrowDetailView(APIView):
    """
    GET — Retrieve full details of a single escrow.
    Only the creator or receiver can view it.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self, pk, user):
        try:
            escrow = (
                Escrow.objects
                .select_related("created_by", "receiver")
                .prefetch_related("images", "documents", "installments")
                .get(pk=pk)
            )
        except Escrow.DoesNotExist:
            return None, "Escrow not found."

        if escrow.created_by != user and escrow.receiver != user:
            return None, "You do not have access to this escrow."

        return escrow, None

    def get(self, request, pk):
        escrow, error = self.get_object(pk, request.user)
        if error:
            return Response({"error": error}, status=status.HTTP_404_NOT_FOUND)

        serializer = EscrowDetailSerializer(escrow)
        return Response({"success": True, "escrow": serializer.data}, status=status.HTTP_200_OK)


# ──────────────────────────────────────────────
# User Search
# ──────────────────────────────────────────────

class UserSearchView(APIView):
    """
    GET — Search users by their email or full name for receiver selection.
    Query param: ?q=searchterm
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        query = request.query_params.get("q", "").strip()
        
        if not query:
            return Response({"success": True, "results": []}, status=status.HTTP_200_OK)

        # Allow searching by leading '@' as a UX convenience
        if query.startswith("@"):
            query = query[1:]

        users = User.objects.filter(
            username__icontains=query,
            is_active=True
        ).exclude(id=request.user.id)[:15]

        serializer = ReceiverSerializer(users, many=True)
        return Response({"success": True, "results": serializer.data}, status=status.HTTP_200_OK)


class OrderHistory(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = OrderHistorySerializer

    def get_queryset(self):
        user = self.request.user
        queryset = Escrow.objects.filter(
            Q(created_by=user) | Q(receiver=user)
        ).select_related("created_by", "receiver")

        status_param = self.request.query_params.get("status")
        role_param = self.request.query_params.get("role")
        search_param = self.request.query_params.get("search")

        if status_param:
            queryset = queryset.filter(status__iexact=status_param)

        if role_param:
            role_param = role_param.lower()
            if role_param == "seller":
                queryset = queryset.filter(
                    Q(created_by=user, role=Escrow.Role.SELLER) |
                    Q(receiver=user, role=Escrow.Role.BUYER)
                )
            elif role_param == "buyer":
                queryset = queryset.filter(
                    Q(created_by=user, role=Escrow.Role.BUYER) |
                    Q(receiver=user, role=Escrow.Role.SELLER)
                )

        if search_param:
            queryset = queryset.filter(
                Q(product_name__icontains=search_param) |
                Q(order_id__icontains=search_param)
            )

        return queryset.order_by("-created_at")


class OrderHistoryDetailView(APIView):
    """
    GET — Retrieve a simplified order history detail (timeline) for a single escrow.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        try:
            escrow = Escrow.objects.select_related("created_by", "receiver").prefetch_related("images", "status_history").get(pk=pk)
        except Escrow.DoesNotExist:
            return Response({"error": "Escrow not found."}, status=status.HTTP_404_NOT_FOUND)

        if escrow.created_by != request.user and escrow.receiver != request.user:
            return Response({"error": "You do not have access to this escrow."}, status=status.HTTP_403_FORBIDDEN)

        serializer = OrderHistoryDetailSerializer(escrow, context={"request": request})
        return Response({"success": True, "detail": serializer.data}, status=status.HTTP_200_OK)


class RecentEscrowsView(generics.ListAPIView):
    """
    GET — Retrieve the 5 most recent escrows for the authenticated user.
    Uses the same lightweight serializer as Order History.
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = OrderHistorySerializer

    def get_queryset(self):
        user = self.request.user
        queryset = Escrow.objects.filter(
            Q(created_by=user) | Q(receiver=user)
        ).select_related("created_by", "receiver").order_by("-created_at")[:5]
        return queryset


class EscrowAcceptView(APIView):
    """
    POST — The receiver (the other party) accepts the Escrow request.
    Only callable when status is CREATED.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            escrow = Escrow.objects.get(pk=pk)
        except Escrow.DoesNotExist:
            return Response({"error": "Escrow not found."}, status=status.HTTP_404_NOT_FOUND)

        if escrow.receiver != request.user:
            return Response({"error": "Only the receiver can accept this Escrow."}, status=status.HTTP_403_FORBIDDEN)

        if escrow.status != Escrow.Status.CREATED:
            return Response({"error": f"Escrow cannot be accepted from '{escrow.get_status_display()}' state."}, status=status.HTTP_400_BAD_REQUEST)

        if escrow.role == Escrow.Role.SELLER:
            wallet = getattr(request.user, "wallet", None)
            total_amount_val = float(escrow.total_amount)
            if not wallet or float(wallet.balance) < total_amount_val:
                return Response({"error": "Insufficient wallet balance to accept this escrow. Please add funds to your wallet."}, status=status.HTTP_400_BAD_REQUEST)

        from django.db import transaction
        with transaction.atomic():
            if escrow.role == Escrow.Role.SELLER:
                wallet = request.user.wallet
                wallet.balance = float(wallet.balance) - float(escrow.total_amount)
                wallet.save(update_fields=["balance", "updated_at"])

            escrow.status = Escrow.Status.ACCEPTED
            escrow.save()

        from app.notification.utils import send_notification
        send_notification(
            user=escrow.created_by,
            title="Escrow Accepted",
            body=f"{request.user.username} has accepted your escrow request.",
            event_type="escrow_accepted",
            reference_id=str(escrow.id)
        )

        return Response({"success": True, "message": "Escrow accepted successfully.", "status": escrow.status}, status=status.HTTP_200_OK)


class EscrowSendProductView(APIView):
    """
    POST — The seller party marks the product/service as sent (shipped).

    The seller party is:
      - created_by  when escrow.role == 'seller'  (creator is the seller)
      - receiver    when escrow.role == 'buyer'   (receiver is the seller)

    Allowed when status == ACCEPTED  →  transitions to IN_PROGRESS.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            escrow = Escrow.objects.get(pk=pk)
        except Escrow.DoesNotExist:
            return Response({"error": "Escrow not found."}, status=status.HTTP_404_NOT_FOUND)

        user = request.user
        # Determine who is the seller in this escrow
        if escrow.role == Escrow.Role.SELLER:
            seller_user = escrow.created_by
        else:
            seller_user = escrow.receiver

        if user != seller_user:
            return Response({"error": "Only the seller can mark the product as sent."}, status=status.HTTP_403_FORBIDDEN)

        if escrow.status != Escrow.Status.ACCEPTED:
            return Response({"error": f"Product can only be sent when status is 'Accepted'. Current: '{escrow.get_status_display()}'."}, status=status.HTTP_400_BAD_REQUEST)

        from app.excrow.models import EscrowDeliveryProof
        from django.db import transaction
        
        with transaction.atomic():
            escrow.status = Escrow.Status.IN_PROGRESS
            escrow.save()
            
            proofs = request.FILES.getlist("proofs") or request.FILES.getlist("proofs[]")
            if proofs:
                EscrowDeliveryProof.objects.bulk_create([
                    EscrowDeliveryProof(escrow=escrow, file=f) for f in proofs
                ])

        buyer_user = escrow.receiver if escrow.role == Escrow.Role.SELLER else escrow.created_by
        from app.notification.utils import send_notification
        send_notification(
            user=buyer_user,
            title="Product Shipped",
            body=f"The seller has marked your product as shipped.",
            event_type="escrow_shipped",
            reference_id=str(escrow.id)
        )

        return Response({"success": True, "message": "Product marked as sent. Status is now IN_PROGRESS.", "status": escrow.status}, status=status.HTTP_200_OK)


class EscrowDeliveredView(APIView):
    """
    POST — The buyer party confirms delivery.

    The buyer party is:
      - created_by  when escrow.role == 'buyer'   (creator is the buyer)
      - receiver    when escrow.role == 'seller'  (receiver is the buyer)

    Allowed when status == IN_PROGRESS  →  transitions to DELIVERED.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            escrow = Escrow.objects.get(pk=pk)
        except Escrow.DoesNotExist:
            return Response({"error": "Escrow not found."}, status=status.HTTP_404_NOT_FOUND)

        user = request.user
        # Determine who is the buyer in this escrow
        if escrow.role == Escrow.Role.BUYER:
            buyer_user = escrow.created_by
        else:
            buyer_user = escrow.receiver

        if user != buyer_user:
            return Response({"error": "Only the buyer can confirm delivery."}, status=status.HTTP_403_FORBIDDEN)

        if escrow.status != Escrow.Status.IN_PROGRESS:
            return Response({"error": f"Delivery can only be confirmed when status is 'In Progress'. Current: '{escrow.get_status_display()}'."}, status=status.HTTP_400_BAD_REQUEST)

        escrow.status = Escrow.Status.DELIVERED
        escrow.save()

        seller_user = escrow.created_by if escrow.role == Escrow.Role.SELLER else escrow.receiver
        from app.notification.utils import send_notification
        send_notification(
            user=seller_user,
            title="Product Delivered",
            body=f"The buyer has marked your product as delivered.",
            event_type="escrow_delivered",
            reference_id=str(escrow.id)
        )

        return Response({"success": True, "message": "Delivery confirmed. Status updated to Delivered.", "status": escrow.status}, status=status.HTTP_200_OK)


class EscrowDisputeView(APIView):
    """
    POST — The buyer party initiates a dispute.

    Allowed when status == DELIVERED  →  transitions to DISPUTE_IN_PROGRESS.
    Expects multipart/form-data: reason, note, images (multiple)
    """
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, pk):
        try:
            escrow = Escrow.objects.get(pk=pk)
        except Escrow.DoesNotExist:
            return Response({"error": "Escrow not found."}, status=status.HTTP_404_NOT_FOUND)

        user = request.user
        # Determine who is the buyer in this escrow
        if escrow.role == Escrow.Role.BUYER:
            buyer_user = escrow.created_by
        else:
            buyer_user = escrow.receiver

        if user != buyer_user:
            return Response({"error": "Only the buyer can initiate a dispute."}, status=status.HTTP_403_FORBIDDEN)

        if escrow.status != Escrow.Status.DELIVERED:
            return Response({"error": f"Dispute can only be initiated when status is 'Delivered'. Current: '{escrow.get_status_display()}'."}, status=status.HTTP_400_BAD_REQUEST)

        if not escrow.can_dispute:
            return Response({"error": "The 24-hour window to initiate a dispute has expired."}, status=status.HTTP_400_BAD_REQUEST)

        reason = request.data.get("reason", "").strip()
        note = request.data.get("note", "").strip()
        images = request.FILES.getlist("images")

        if not reason or not note:
            return Response({"error": "Reason and note are required."}, status=status.HTTP_400_BAD_REQUEST)
        
        valid_reasons = [choice[0] for choice in EscrowDispute.ReasonChoices.choices]
        if reason not in valid_reasons:
            return Response({"error": f"Invalid reason '{reason}'. Allowed values: {', '.join(valid_reasons)}."}, status=status.HTTP_400_BAD_REQUEST)

        if not images:
            return Response({"error": "At least one image is required for a dispute."}, status=status.HTTP_400_BAD_REQUEST)

        # Save dispute
        dispute = EscrowDispute.objects.create(
            escrow=escrow,
            raised_by=user,
            reason=reason,
            note=note
        )

        # Save images
        EscrowDisputeImage.objects.bulk_create([
            EscrowDisputeImage(dispute=dispute, image=img)
            for img in images
        ])

        # Update escrow status to dispute in progress
        escrow.status = Escrow.Status.DISPUTE_IN_PROGRESS
        escrow.save()

        seller_user = escrow.created_by if escrow.role == Escrow.Role.SELLER else escrow.receiver
        from app.notification.utils import send_notification
        send_notification(
            user=seller_user,
            title="Issue Reported",
            body=f"The buyer has opened a dispute on your escrow.",
            event_type="escrow_disputed",
            reference_id=str(escrow.id)
        )

        # Fire Gemini AI analysis in background
        try:
            from app.ai.tasks import analyze_dispute
            analyze_dispute.delay(str(dispute.id))
        except Exception as exc:
            import logging
            logging.getLogger("app").warning(
                "Could not enqueue analyze_dispute task for %s: %s", dispute.id, exc
            )

        return Response({"success": True, "message": "Dispute initiated. AI analysis started in background.", "status": escrow.status}, status=status.HTTP_200_OK)



# ──────────────────────────────────────────────
# Dispute / Issue Escrows
# ──────────────────────────────────────────────

class DisputeListView(APIView):
    """
    GET — List all escrows connected to the authenticated user that are
    currently in a dispute or resolution state:

      - issue_raised        — a party has raised an issue
      - dispute_in_progress — a formal dispute is actively in progress
      - under_review        — the dispute is under review
      - return_in_progress  — a return has been initiated
      - refunded            — a refund has been processed / resolved

    The user is considered "connected" if they are either the creator
    (created_by) or the receiver of the escrow.

    Query params (all optional):
      ?status=<status>  — filter to a single dispute status from the list above
      ?search=<term>    — search by product name or order ID (case-insensitive)
    """
    permission_classes = [permissions.IsAuthenticated]

    DISPUTE_STATUSES = [
        Escrow.Status.ISSUE_RAISED,
        Escrow.Status.DISPUTE_IN_PROGRESS,
        Escrow.Status.UNDER_REVIEW,
        Escrow.Status.RETURN_IN_PROGRESS,
        Escrow.Status.REFUNDED,
    ]

    def get(self, request):
        user = request.user

        queryset = (
            Escrow.objects
            .filter(
                Q(created_by=user) | Q(receiver=user),
                status__in=self.DISPUTE_STATUSES,
            )
            .select_related("created_by", "receiver")
            .prefetch_related("images")
            .order_by("-updated_at")
        )

        # Optional status filter (must be one of the dispute statuses)
        status_param = request.query_params.get("status", "").strip().lower()
        if status_param:
            allowed = {s.value for s in self.DISPUTE_STATUSES}
            if status_param not in allowed:
                return Response(
                    {
                        "error": (
                            f"Invalid status '{status_param}'. "
                            f"Allowed values: {', '.join(sorted(allowed))}."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            queryset = queryset.filter(status=status_param)

        # Optional search by product name or order ID
        search_param = request.query_params.get("search", "").strip()
        if search_param:
            queryset = queryset.filter(
                Q(product_name__icontains=search_param) |
                Q(order_id__icontains=search_param)
            )

        paginator = PageNumberPagination()
        paginated = paginator.paginate_queryset(queryset, request, view=self)

        serializer = DisputeListSerializer(paginated, many=True, context={"request": request})
        return Response(
            {
                "success": True,
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": serializer.data,
            },
            status=status.HTTP_200_OK,
        )


# ──────────────────────────────────────────────
# Seller responds to AI decision
# ──────────────────────────────────────────────

class SellerDisputeResponseView(APIView):
    """
    POST — Seller accepts or rejects the AI decision.
    Only valid when dispute.status == 'awaiting_seller'.
    Body: { "action": "accept" | "reject" }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            dispute = EscrowDispute.objects.select_related("escrow").get(pk=pk)
        except EscrowDispute.DoesNotExist:
            return Response({"error": "Dispute not found."}, status=status.HTTP_404_NOT_FOUND)

        escrow = dispute.escrow
        user   = request.user
        seller = escrow.created_by if escrow.role == Escrow.Role.SELLER else escrow.receiver

        if user != seller:
            return Response({"error": "Only the seller can respond to this dispute."}, status=status.HTTP_403_FORBIDDEN)

        if dispute.status != EscrowDispute.StatusChoices.AWAITING_SELLER:
            return Response(
                {"error": f"Dispute is not awaiting a seller response. Current status: {dispute.status}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        action = request.data.get("action", "").strip().lower()
        if action not in ("accept", "reject"):
            return Response({"error": "action must be 'accept' or 'reject'."}, status=status.HTTP_400_BAD_REQUEST)

        from django.db import transaction as db_transaction
        with db_transaction.atomic():
            if action == "accept":
                dispute.status          = EscrowDispute.StatusChoices.ACCEPTED
                dispute.seller_response = EscrowDispute.SellerResponseChoices.ACCEPTED
                dispute.decision_reason = (dispute.decision_reason or "") + "\n\nSeller accepted the AI decision."
                msg = "Dispute accepted. The buyer's claim has been upheld."
            else:
                dispute.status          = EscrowDispute.StatusChoices.PENDING_KYC
                dispute.seller_response = EscrowDispute.SellerResponseChoices.REJECTED
                dispute.decision_reason = (dispute.decision_reason or "") + "\n\nSeller rejected the AI decision. Case sent to KYC review."
                msg = "Escalated to KYC review. If buyer is confirmed correct, a $10 review fee will be charged to the seller."
            dispute.save()

        return Response({"success": True, "message": msg, "status": dispute.status}, status=status.HTTP_200_OK)


# ──────────────────────────────────────────────
# KYC Resolver finalises a dispute manually
# ──────────────────────────────────────────────

class KYCDisputeResolveView(APIView):
    """
    POST — KYC Specialist or Admin resolves a dispute in pending_kyc.
    Body: { "decision": "buyer_correct" | "seller_correct", "reason": "..." }
    If buyer_correct AND seller previously rejected AI: charge $10 from seller wallet.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        if request.user.role not in ("kyc", "admin"):
            return Response(
                {"error": "You do not have permission to resolve disputes. KYC or Admin role required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            dispute = EscrowDispute.objects.select_related("escrow").get(pk=pk)
        except EscrowDispute.DoesNotExist:
            return Response({"error": "Dispute not found."}, status=status.HTTP_404_NOT_FOUND)


        if dispute.status != EscrowDispute.StatusChoices.PENDING_KYC:
            return Response(
                {"error": f"Dispute is not in pending_kyc. Current: {dispute.status}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        decision = request.data.get("decision", "").strip().lower()
        reason   = request.data.get("reason", "").strip()

        if decision not in ("buyer_correct", "seller_correct"):
            return Response({"error": "decision must be 'buyer_correct' or 'seller_correct'."}, status=status.HTTP_400_BAD_REQUEST)

        from django.db import transaction as db_transaction
        penalty_applied = False

        with db_transaction.atomic():
            if decision == "buyer_correct":
                dispute.status = EscrowDispute.StatusChoices.ACCEPTED
                # Charge $10 if seller escalated and lost
                if dispute.seller_response == EscrowDispute.SellerResponseChoices.REJECTED and not dispute.penalty_charged:
                    seller = dispute.escrow.created_by if dispute.escrow.role == Escrow.Role.SELLER else dispute.escrow.receiver
                    try:
                        from app.profile.models import Wallet
                        wallet = Wallet.objects.select_for_update().get(user=seller)
                        wallet.balance = max(0, float(wallet.balance) - 10)
                        wallet.save()
                        dispute.penalty_charged = True
                        penalty_applied = True
                    except Exception:
                        pass
            else:
                dispute.status = EscrowDispute.StatusChoices.DECLINED

            if reason:
                dispute.decision_reason = reason
            dispute.save()

        return Response({
            "success": True,
            "status": dispute.status,
            "penalty_charged": penalty_applied,
            "message": (
                f"Dispute resolved: {'buyer wins' if decision == 'buyer_correct' else 'seller wins'}."
                + (" Seller charged $10 review penalty." if penalty_applied else "")
            ),
        }, status=status.HTTP_200_OK)


class KYCDisputeListView(APIView):
    """
    GET — Fetch disputes for KYC specialist.
    ?assigned=false to see unassigned, ?assigned=true for mine
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if request.user.role not in ("kyc", "admin"):
            return Response({"error": "Permission denied. KYC or Admin role required."}, status=status.HTTP_403_FORBIDDEN)

        queryset = EscrowDispute.objects.filter(status=EscrowDispute.StatusChoices.PENDING_KYC).select_related("escrow").order_by("-created_at")
        
        assigned = request.query_params.get("assigned", "").lower()
        if assigned == "false":
            queryset = queryset.filter(assigned_to__isnull=True)
        elif assigned == "true":
            queryset = queryset.filter(assigned_to=request.user)

        from rest_framework.pagination import PageNumberPagination
        paginator = PageNumberPagination()
        paginated = paginator.paginate_queryset(queryset, request, view=self)

        data = []
        for d in paginated:
            data.append({
                "id": d.id,
                "escrow_id": d.escrow.id,
                "order_id": d.escrow.order_id,
                "product_name": d.escrow.product_name,
                "reason": d.reason,
                "created_at": d.created_at,
                "assigned_to": d.assigned_to.id if d.assigned_to else None,
            })
            
        return Response(
            {
                "success": True,
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": data,
            },
            status=status.HTTP_200_OK,
        )


class KYCDisputeAssignView(APIView):
    """
    POST — Assign dispute to self and create buyer/seller conversations.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        if request.user.role not in ("kyc", "admin"):
            return Response({"error": "Permission denied. KYC or Admin role required."}, status=status.HTTP_403_FORBIDDEN)

        try:
            dispute = EscrowDispute.objects.select_related("escrow", "escrow__created_by", "escrow__receiver").get(pk=pk)
        except EscrowDispute.DoesNotExist:
            return Response({"error": "Dispute not found."}, status=status.HTTP_404_NOT_FOUND)

        if dispute.status != EscrowDispute.StatusChoices.PENDING_KYC:
            return Response({"error": "Dispute is not pending KYC review."}, status=status.HTTP_400_BAD_REQUEST)
        
        if dispute.assigned_to:
            if dispute.assigned_to == request.user:
                return Response({"success": True, "message": "Already assigned to you."})
            return Response({"error": "Dispute is already assigned to someone else."}, status=status.HTTP_400_BAD_REQUEST)

        from app.messaging.models import Conversation
        
        dispute.assigned_to = request.user
        dispute.save()

        # Create Conversation with Seller
        seller = dispute.escrow.created_by if dispute.escrow.role == Escrow.Role.SELLER else dispute.escrow.receiver
        conv_seller = Conversation.objects.create(
            title=f"Dispute request #{dispute.escrow.order_id} (Seller)",
            is_dispute=True
        )
        conv_seller.participants.add(request.user, seller)

        # Create Conversation with Buyer
        buyer = dispute.escrow.receiver if dispute.escrow.role == Escrow.Role.SELLER else dispute.escrow.created_by
        conv_buyer = Conversation.objects.create(
            title=f"Dispute request #{dispute.escrow.order_id} (Buyer)",
            is_dispute=True
        )
        conv_buyer.participants.add(request.user, buyer)

        return Response({"success": True, "message": "Dispute assigned successfully and conversations created."}, status=status.HTTP_200_OK)


# ──────────────────────────────────────────────
# Rate a Buyer
# ──────────────────────────────────────────────

class EscrowRatingView(APIView):
    """
    POST /api/escrow/<pk>/rate/
    Allows the user to rate the other party after the escrow is DELIVERED.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            escrow = Escrow.objects.select_related('created_by', 'receiver').get(pk=pk)
        except Escrow.DoesNotExist:
            return Response({"error": "Escrow not found."}, status=status.HTTP_404_NOT_FOUND)

        user = request.user

        if user not in [escrow.created_by, escrow.receiver]:
            return Response({"error": "Only participants can rate."}, status=status.HTTP_403_FORBIDDEN)
        
        # Determine rated user
        rated_user = escrow.receiver if user == escrow.created_by else escrow.created_by

        if escrow.status not in [Escrow.Status.DELIVERED, Escrow.Status.COMPLETED]:
            return Response(
                {"error": "Rating is only available after delivery is confirmed."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Prevent duplicate ratings from the same user
        if EscrowRating.objects.filter(escrow=escrow, rated_by=user).exists():
            return Response({"error": "You have already rated this user."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = EscrowRatingSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"error": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        rating = serializer.save(
            escrow=escrow,
            rated_by=user,
            rated_user=rated_user
        )

        # Send notification to the rated user
        try:
            from app.notification.utils import send_notification
            send_notification(
                user=rated_user,
                title="You've been rated!",
                body=f"{user.username} gave you {rating.stars}★ on your recent escrow.",
                event_type="rating_received",
                reference_id=str(escrow.id)
            )
        except Exception:
            pass

        return Response(
            {
                "success": True,
                "message": "Rating submitted successfully.",
                "rating": EscrowRatingReadSerializer(rating).data
            },
            status=status.HTTP_201_CREATED
        )

    def get(self, request, pk):
        """Get the rating given by the current user for a specific escrow."""
        if not request.user.is_authenticated:
            return Response({"rating": None}, status=status.HTTP_200_OK)
            
        try:
            rating = EscrowRating.objects.select_related('rated_by', 'rated_user').get(escrow_id=pk, rated_by=request.user)
        except EscrowRating.DoesNotExist:
            return Response({"rating": None}, status=status.HTTP_200_OK)
        
        return Response({
            "rating": EscrowRatingReadSerializer(rating).data
        }, status=status.HTTP_200_OK)


# ──────────────────────────────────────────────
# Release Installment
# ──────────────────────────────────────────────

class EscrowReleaseInstallmentView(APIView):
    """
    POST /api/escrow/installments/<pk>/release/
    Allows the buyer to release an unpaid installment to the seller.
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        from django.utils import timezone
        
        try:
            installment = EscrowInstallment.objects.select_related("escrow").get(pk=pk)
        except EscrowInstallment.DoesNotExist:
            return Response({"error": "Installment not found."}, status=status.HTTP_404_NOT_FOUND)

        escrow = installment.escrow
        user = request.user

        buyer_id = escrow.created_by_id if escrow.role == Escrow.Role.BUYER else escrow.receiver_id

        if user.id != buyer_id:
            return Response({"error": "Only the buyer can release installments."}, status=status.HTTP_403_FORBIDDEN)

        if installment.is_paid:
            return Response({"error": "This installment is already released."}, status=status.HTTP_400_BAD_REQUEST)
        
        if escrow.status != Escrow.Status.IN_PROGRESS:
            return Response({"error": "You can only release installments after the seller has sent the product."}, status=status.HTTP_400_BAD_REQUEST)

        # Enforce sequential release
        previous_unpaid = EscrowInstallment.objects.filter(
            escrow=escrow,
            order__lt=installment.order,
            is_paid=False
        ).exists()

        if previous_unpaid:
            return Response({"error": "You must release previous installments first."}, status=status.HTTP_400_BAD_REQUEST)

        # Release
        installment.is_paid = True
        installment.paid_at = timezone.now()
        installment.save()

        # Check if all installments are paid
        all_paid = not EscrowInstallment.objects.filter(escrow=escrow, is_paid=False).exists()
        
        if all_paid:
            # Active release implies acceptance. Move straight to completed.
            escrow.status = Escrow.Status.COMPLETED
            escrow.save()
            EscrowStatusHistory.objects.create(escrow=escrow, status=Escrow.Status.COMPLETED)
        else:
            # Revert to ACCEPTED so the seller has to "Send Product" again for the next installment
            escrow.status = Escrow.Status.ACCEPTED
            escrow.save()
            EscrowStatusHistory.objects.create(escrow=escrow, status=Escrow.Status.ACCEPTED)

        return Response({
            "success": True, 
            "message": "Installment released successfully."
        }, status=status.HTTP_200_OK)
