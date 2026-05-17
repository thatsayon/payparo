from rest_framework import generics, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser

from django.db.models import Q
from django.contrib.auth import get_user_model

from .models import (
    Escrow, 
    EscrowImage, 
    EscrowDocument, 
    EscrowInstallment, 
    EscrowStatusHistory
)
from .serializers import (
    EscrowCreateSerializer,
    EscrowListSerializer,
    EscrowDetailSerializer,
    ReceiverSerializer,
    OrderHistorySerializer,
    OrderHistoryDetailSerializer,
    DisputeListSerializer,
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
        required_fields = ['role', 'item_type', 'product_name', 'description', 'payment_option', 'price', 'fee_amount']
        
        missing = []
        for field in required_fields:
            if not data.get(field):
                missing.append(field)
                
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
            with transaction.atomic():
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

        escrow.status = Escrow.Status.ACCEPTED
        escrow.save()

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

        escrow.status = Escrow.Status.IN_PROGRESS
        escrow.save()

        return Response({"success": True, "message": "Product marked as sent. Status updated to In Progress.", "status": escrow.status}, status=status.HTTP_200_OK)


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

        return Response({"success": True, "message": "Delivery confirmed. Status updated to Delivered.", "status": escrow.status}, status=status.HTTP_200_OK)


class EscrowDisputeView(APIView):
    """
    POST — The buyer party initiates a dispute.

    Allowed when status == DELIVERED  →  transitions to DISPUTE_IN_PROGRESS.
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
            return Response({"error": "Only the buyer can initiate a dispute."}, status=status.HTTP_403_FORBIDDEN)

        if escrow.status != Escrow.Status.DELIVERED:
            return Response({"error": f"Dispute can only be initiated when status is 'Delivered'. Current: '{escrow.get_status_display()}'."}, status=status.HTTP_400_BAD_REQUEST)

        escrow.status = Escrow.Status.DISPUTE_IN_PROGRESS
        escrow.save()

        return Response({"success": True, "message": "Dispute initiated. Status updated to Dispute In Progress.", "status": escrow.status}, status=status.HTTP_200_OK)


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

