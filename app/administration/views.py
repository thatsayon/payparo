from django.db.models import CharField, Count, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce

from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from app.accounts.models import KYCSubmission, UserAccount
from app.excrow.models import Escrow
from rest_framework.pagination import PageNumberPagination
from .serializers import UserManagementUserSerializer

class UserManagementView(generics.ListAPIView):
    permission_classes = [permissions.IsAdminUser]
    serializer_class = UserManagementUserSerializer
    pagination_class = PageNumberPagination

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



class EscrowTransactionsView(generics.ListAPIView):
    permission_classes = [permissions.IsAdminUser]
    serializer_class = __import__('app.administration.serializers', fromlist=['EscrowTransactionsSerializer']).EscrowTransactionsSerializer
    pagination_class = PageNumberPagination

    def get_queryset(self):
        return Escrow.objects.select_related('created_by', 'receiver').all()

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        
        # Calculate stats for the dashboard
        total_transactions = Escrow.objects.count()
        
        active_statuses = [
            Escrow.Status.IN_PROGRESS, 
            Escrow.Status.FUNDED, 
            Escrow.Status.ACCEPTED, 
            Escrow.Status.SHIPPED, 
            Escrow.Status.UNDER_REVIEW
        ]
        active_transactions = Escrow.objects.filter(status__in=active_statuses).count()
        
        in_dispute = Escrow.objects.filter(status=Escrow.Status.ISSUE_RAISED).count()
        completed = Escrow.objects.filter(status=Escrow.Status.COMPLETED).count()

        stats = {
            'total_transactions': total_transactions,
            'active_transactions': active_transactions,
            'in_dispute': in_dispute,
            'completed': completed,
        }

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
            # Inject stats into the paginated response
            response.data['stats'] = stats
            return response

        serializer = self.get_serializer(queryset, many=True)
        return Response({
            'stats': stats,
            'results': serializer.data
        })


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
