from django.db.models import Q
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.pagination import PageNumberPagination
from app.excrow.models import EscrowDispute

class KYCAssignedDisputeListView(APIView):
    """
    GET — Fetch disputes assigned to the current KYC Specialist.
    Only users with role "kyc" or "admin" can access this view.

    Supported query parameters:
      - status: Filter by dispute status (e.g. pending_kyc, accepted, declined, etc.)
      - confidence: Filter by minimum AI confidence threshold (e.g. 0.8)
      - min_confidence: Filter by minimum AI confidence threshold (e.g. 0.75)
      - max_confidence: Filter by maximum AI confidence threshold (e.g. 0.95)
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if request.user.role not in ("kyc", "admin"):
            return Response({"error": "Permission denied. KYC or Admin role required."}, status=status.HTTP_403_FORBIDDEN)

        queryset = EscrowDispute.objects.filter(assigned_to=request.user).select_related("escrow", "raised_by").order_by("-created_at")

        # Search parameter filter using q
        q_param = request.query_params.get("q", "").strip()
        if q_param:
            queryset = queryset.filter(
                Q(escrow__product_name__icontains=q_param) |
                Q(escrow__order_id__icontains=q_param) |
                Q(reason__icontains=q_param) |
                Q(note__icontains=q_param)
            )

        # 1. Status filter
        status_param = request.query_params.get("status", "").strip().lower()
        if status_param:
            queryset = queryset.filter(status=status_param)

        # 2. Confidence filters
        confidence_param = request.query_params.get("confidence")
        if confidence_param:
            try:
                queryset = queryset.filter(ai_confidence__gte=float(confidence_param))
            except ValueError:
                return Response({"error": "Invalid confidence format. Must be a decimal number."}, status=status.HTTP_400_BAD_REQUEST)

        min_confidence = request.query_params.get("min_confidence")
        if min_confidence:
            try:
                queryset = queryset.filter(ai_confidence__gte=float(min_confidence))
            except ValueError:
                return Response({"error": "Invalid min_confidence format. Must be a decimal number."}, status=status.HTTP_400_BAD_REQUEST)

        max_confidence = request.query_params.get("max_confidence")
        if max_confidence:
            try:
                queryset = queryset.filter(ai_confidence__lte=float(max_confidence))
            except ValueError:
                return Response({"error": "Invalid max_confidence format. Must be a decimal number."}, status=status.HTTP_400_BAD_REQUEST)

        # 3. Pagination
        paginator = PageNumberPagination()
        paginated_queryset = paginator.paginate_queryset(queryset, request, view=self)

        results = []
        for dispute in paginated_queryset:
            results.append({
                "id": dispute.id,
                "kyc_name": dispute.raised_by.full_name or dispute.raised_by.username,
                "transaction_id": dispute.escrow.order_id,
                "claim_type": dispute.reason,
                "escrow_amount": dispute.escrow.total_amount or dispute.escrow.price,
                "ai_confidence": dispute.ai_confidence,
                "current_status": dispute.status,
            })

        return Response({
            "success": True,
            "count": paginator.page.paginator.count,
            "next": paginator.get_next_link(),
            "previous": paginator.get_previous_link(),
            "results": results
        }, status=status.HTTP_200_OK)


class KYCAssignedDisputeDetailView(APIView):
    """
    GET — Fetch details of a specific dispute assigned to the current KYC Specialist.
    Only users with role "kyc" or "admin" can access this view.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        if request.user.role not in ("kyc", "admin"):
            return Response({"error": "Permission denied. KYC or Admin role required."}, status=status.HTTP_403_FORBIDDEN)

        try:
            # Query the dispute and prefetch relative models
            dispute = EscrowDispute.objects.select_related(
                "escrow",
                "escrow__created_by",
                "escrow__receiver",
                "raised_by",
                "assigned_to"
            ).prefetch_related(
                "escrow__images",
                "images"
            ).get(pk=pk)
        except EscrowDispute.DoesNotExist:
            return Response({"error": "Dispute not found."}, status=status.HTTP_404_NOT_FOUND)

        # Access check: If user is KYC role, it must be assigned to them
        if request.user.role == "kyc" and dispute.assigned_to != request.user:
            return Response({"error": "Access denied. This dispute is not assigned to you."}, status=status.HTTP_403_FORBIDDEN)

        escrow = dispute.escrow

        # Dynamically determine who is the buyer and who is the seller based on escrow role configuration
        if escrow.role == "seller":
            seller = escrow.created_by
            buyer = escrow.receiver
        else:
            seller = escrow.receiver
            buyer = escrow.created_by

        # Build detailed payload
        data = {
            "id": dispute.id,
            "reason": dispute.reason,
            "note": dispute.note,
            "images": [img.image.url for img in dispute.images.all() if img.image],
            "current_status": dispute.status,
            "created_at": dispute.created_at,
            "who_claimed": {
                "id": dispute.raised_by.id,
                "username": dispute.raised_by.username,
                "full_name": dispute.raised_by.full_name,
                "email": dispute.raised_by.email,
            },
            "buyer": {
                "id": buyer.id if buyer else None,
                "username": buyer.username if buyer else "",
                "full_name": buyer.full_name if buyer else "",
                "email": buyer.email if buyer else "",
            },
            "seller": {
                "id": seller.id if seller else None,
                "username": seller.username if seller else "",
                "full_name": seller.full_name if seller else "",
                "email": seller.email if seller else "",
            },
            "escrow_info": {
                "id": escrow.id,
                "order_id": escrow.order_id,
                "product_name": escrow.product_name,
                "description": escrow.description,
                "item_type": escrow.item_type,
                "payment_option": escrow.payment_option,
                "price": escrow.price,
                "fee_amount": escrow.fee_amount,
                "total_amount": escrow.total_amount,
                "currency": escrow.currency,
                "status": escrow.status,
                "created_at": escrow.created_at,
                "images": [img.image.url for img in escrow.images.all() if img.image],
            },
            "ai_result": {
                "decision": dispute.ai_decision,
                "confidence": dispute.ai_confidence,
                "summary": dispute.ai_summary,
            }
        }

        return Response({
            "success": True,
            "dispute": data
        }, status=status.HTTP_200_OK)


class KYCDisputeListView(APIView):
    """
    GET — Fetch unassigned disputes for KYC specialist.
    Only returns disputes that are unassigned (assigned_to__isnull=True).
    Only users with role "kyc" or "admin" can access this view.

    Supported query parameters:
      - status: Filter by dispute status (e.g. pending_kyc, accepted, declined, etc.)
      - confidence: Filter by minimum AI confidence threshold (e.g. 0.8)
      - min_confidence: Filter by minimum AI confidence threshold (e.g. 0.75)
      - max_confidence: Filter by maximum AI confidence threshold (e.g. 0.95)
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if request.user.role not in ("kyc", "admin"):
            return Response({"error": "Permission denied. KYC or Admin role required."}, status=status.HTTP_403_FORBIDDEN)

        # Base queryset: pending KYC and unassigned
        queryset = EscrowDispute.objects.filter(
            status=EscrowDispute.StatusChoices.PENDING_KYC,
            assigned_to__isnull=True
        ).select_related("escrow").order_by("-created_at")

        # Search parameter filter using q
        q_param = request.query_params.get("q", "").strip()
        if q_param:
            queryset = queryset.filter(
                Q(escrow__product_name__icontains=q_param) |
                Q(escrow__order_id__icontains=q_param) |
                Q(reason__icontains=q_param) |
                Q(note__icontains=q_param)
            )

        # 1. Optional status filter (defaults to pending_kyc, but kept for symmetry)
        status_param = request.query_params.get("status", "").strip().lower()
        if status_param:
            queryset = queryset.filter(status=status_param)

        # 2. Confidence filters
        confidence_param = request.query_params.get("confidence")
        if confidence_param:
            try:
                queryset = queryset.filter(ai_confidence__gte=float(confidence_param))
            except ValueError:
                return Response({"error": "Invalid confidence format. Must be a decimal number."}, status=status.HTTP_400_BAD_REQUEST)

        min_confidence = request.query_params.get("min_confidence")
        if min_confidence:
            try:
                queryset = queryset.filter(ai_confidence__gte=float(min_confidence))
            except ValueError:
                return Response({"error": "Invalid min_confidence format. Must be a decimal number."}, status=status.HTTP_400_BAD_REQUEST)

        max_confidence = request.query_params.get("max_confidence")
        if max_confidence:
            try:
                queryset = queryset.filter(ai_confidence__lte=float(max_confidence))
            except ValueError:
                return Response({"error": "Invalid max_confidence format. Must be a decimal number."}, status=status.HTTP_400_BAD_REQUEST)

        # 3. Pagination
        paginator = PageNumberPagination()
        paginated_queryset = paginator.paginate_queryset(queryset, request, view=self)

        def get_ai_status(dispute):
            conf = dispute.ai_confidence
            decision = dispute.ai_decision
            if conf is not None and conf >= 0.70:
                if decision == "buyer_likely_correct":
                    return "favor_buyer"
                elif decision == "seller_likely_correct":
                    return "favor_sellar"
            return "need_human_review"

        data = []
        for d in paginated_queryset:
            data.append({
                "id": d.id,
                "escrow_id": d.escrow.id,
                "order_id": d.escrow.order_id,
                "product_name": d.escrow.product_name,
                "escrow_price": d.escrow.price,
                "reason": d.reason,
                "created_at": d.created_at,
                "ai_status": get_ai_status(d),
            })

        return Response({
            "success": True,
            "count": paginator.page.paginator.count,
            "next": paginator.get_next_link(),
            "previous": paginator.get_previous_link(),
            "results": data,
        }, status=status.HTTP_200_OK)


