from django.db.models import CharField, Count, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce

from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from app.accounts.models import KYCSubmission, UserAccount
from app.excrow.models import Escrow
from .serializers import UserManagementPayloadSerializer, EscrowTransactionsSerializer


class UserManagementView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    status_labels = {
        "pending": "Pending",
        "under_review": "Under Review",
        "approved": "Approved",
        "rejected": "Rejected",
        "not_submitted": "Not Submitted",
    }

    status_badges = {
        "pending": "pending",
        "under_review": "review",
        "approved": "approved",
        "rejected": "rejected",
        "not_submitted": "muted",
    }

    def get(self, request, *args, **kwargs):
        if not request.user.is_staff:
            return Response({"detail": "Staff access required."}, status=status.HTTP_403_FORBIDDEN)

        payload = {
            "page_title": "User Management",
            "subtitle": "Manage users and review KYC submissions",
            "total_users": self.get_queryset().count(),
            "search_query": request.GET.get("q", "").strip(),
            "selected_status": request.GET.get("status", "all").strip().lower() or "all",
            "status_options": [
                {"value": "all", "label": "All Status"},
                {"value": "pending", "label": "Pending"},
                {"value": "under_review", "label": "Under Review"},
                {"value": "approved", "label": "Approved"},
                {"value": "rejected", "label": "Rejected"},
                {"value": "not_submitted", "label": "Not Submitted"},
            ],
            "users": self.serialize_users(self.get_queryset()),
        }

        serializer = UserManagementPayloadSerializer(payload)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def get_queryset(self):
        latest_kyc_status = Subquery(
            KYCSubmission.objects.filter(user=OuterRef("pk")).order_by("-submitted_at").values("status")[:1],
            output_field=CharField(),
        )

        queryset = (
            UserAccount.objects.annotate(
                annotated_kyc_status=Coalesce(latest_kyc_status, Value("not_submitted")),
                transaction_count=Count("wallet__transactions", distinct=True),
            )
            .order_by("full_name", "email")
        )

        search_query = self.request.GET.get("q", "").strip()
        status_filter = self.request.GET.get("status", "all").strip().lower()

        if search_query:
            queryset = queryset.filter(
                Q(full_name__icontains=search_query)
                | Q(email__icontains=search_query)
                | Q(username__icontains=search_query)
                | Q(id__icontains=search_query)
            )

        if status_filter and status_filter != "all":
            queryset = queryset.filter(annotated_kyc_status=status_filter)

        return queryset

    def serialize_users(self, queryset):
        users = []
        for user in queryset:
            status_value = getattr(user, "annotated_kyc_status", "not_submitted")
            users.append(
                {
                    "id": str(user.id),
                    "full_name": user.full_name or user.username or user.email,
                    "email": user.email,
                    "kyc_status": status_value,
                    "kyc_label": self.status_labels.get(status_value, status_value.replace("_", " ").title()),
                    "badge_class": self.status_badges.get(status_value, "muted"),
                    "transaction_count": getattr(user, "transaction_count", 0),
                }
            )
        return users


class EscrowTransactionsView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        escrows = Escrow.objects.all()
        escrow_serializer = EscrowTransactionsSerializer(data=escrows, many=True)
        return Response(
            escrow_serializer.data,
            status=status.HTTP_200_OK
        )
        pass


class KYCSubmissionListView(generics.ListAPIView):
    """
    List all KYC submissions with pagination and search filter.
    GET /administration/kyc-requests/?q=<search>&status=<status>
    """
    permission_classes = [permissions.IsAdminUser]
    serializer_class = __import__('app.administration.serializers', fromlist=['KYCSubmissionListSerializer']).KYCSubmissionListSerializer

    def get_queryset(self):
        queryset = KYCSubmission.objects.select_related('user').order_by('-submitted_at')
        search_query = self.request.query_params.get("q", "").strip()
        status_filter = self.request.query_params.get("status", "").strip().lower()

        if search_query:
            queryset = queryset.filter(
                Q(user__full_name__icontains=search_query)
                | Q(user__email__icontains=search_query)
                | Q(user__username__icontains=search_query)
                | Q(id__icontains=search_query)
            )

        if status_filter:
            queryset = queryset.filter(status=status_filter)

        return queryset


class KYCSubmissionStatusUpdateView(generics.UpdateAPIView):
    """
    Update a KYC submission's status (e.g. approve or reject).
    PATCH /administration/kyc-requests/<id>/status/
    """
    permission_classes = [permissions.IsAdminUser]
    serializer_class = __import__('app.administration.serializers', fromlist=['KYCStatusUpdateSerializer']).KYCStatusUpdateSerializer
    queryset = KYCSubmission.objects.all()
    http_method_names = ['patch']
