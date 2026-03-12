from rest_framework import generics, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser

from django.db.models import Q
from django.contrib.auth import get_user_model

from .models import Escrow
from .serializers import (
    EscrowCreateSerializer,
    EscrowListSerializer,
    EscrowDetailSerializer,
    ReceiverSerializer,
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
        if hasattr(request.data, "copy"):
            data = request.data.copy()
        else:
            data = dict(request.data)

        # Helper to extract list from QueryDict for cases like field[], field[0], etc.
        def extract_list(field_name, source, is_file=False):
            if hasattr(source, "getlist"):
                val = source.getlist(field_name) or source.getlist(f"{field_name}[]")
            else:
                val = source.get(field_name) or source.get(f"{field_name}[]")
                if val and not isinstance(val, list):
                    val = [val]

            # Special case for Singular (image, document)
            if not val:
                singular = field_name[:-1] if field_name.endswith('s') else field_name
                if hasattr(source, "getlist"):
                    val = source.getlist(singular)
                else:
                    s_val = source.get(singular)
                    val = [s_val] if s_val and not isinstance(s_val, list) else s_val

            # Fallback for indexed forms (images[0], images[1], etc)
            if not val:
                # Need to check keys
                keys = sorted([k for k in source.keys() if k.startswith(f"{field_name}[")])
                if keys:
                    val = [source[k] for k in keys]
            return val or []

        images = extract_list("images", request.FILES, is_file=True)
        if images:
            if hasattr(data, "setlist"):
                data.setlist("images", images)
            else:
                data["images"] = images
                
        documents = extract_list("documents", request.FILES, is_file=True)
        if documents:
            if hasattr(data, "setlist"):
                data.setlist("documents", documents)
            else:
                data["documents"] = documents

        installments = extract_list("installments", request.data)
        if installments:
            if hasattr(data, "setlist"):
                data.setlist("installments", installments)
            else:
                data["installments"] = installments

        serializer = EscrowCreateSerializer(
            data=data,
            context={"request": request},
        )

        if not serializer.is_valid():
            return Response(
                {"error": _first_error(serializer)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        escrow = serializer.save()

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
