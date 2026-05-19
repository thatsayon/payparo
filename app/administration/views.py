from datetime import timedelta
from decimal import Decimal

from django.db.models import CharField, Count, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination

from app.accounts.models import KYCSubmission, UserAccount
from app.administration.models import FeeConfiguration
from app.excrow.models import Escrow
from .serializers import (
    UserManagementUserSerializer,
    EscrowTransactionsSerializer,
    KYCSubmissionListSerializer,
    KYCStatusUpdateSerializer,
    EscrowDetailPageSerializer,
    AdminProfilePageSerializer,
    AdminProfileUpdateSerializer,
    AdminPasswordUpdateSerializer,
    AdminWithdrawRequestListSerializer,
)


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
    serializer_class = EscrowTransactionsSerializer
    pagination_class = PageNumberPagination

    def get_queryset(self):
        return Escrow.objects.select_related("created_by", "receiver").all()

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())

        total_transactions = Escrow.objects.count()

        active_statuses = [
            Escrow.Status.IN_PROGRESS,
            Escrow.Status.FUNDED,
            Escrow.Status.ACCEPTED,
            Escrow.Status.SHIPPED,
            Escrow.Status.UNDER_REVIEW,
        ]
        active_transactions = Escrow.objects.filter(status__in=active_statuses).count()

        in_dispute = Escrow.objects.filter(status=Escrow.Status.ISSUE_RAISED).count()
        completed = Escrow.objects.filter(status=Escrow.Status.COMPLETED).count()

        stats = {
            "total_transactions": total_transactions,
            "active_transactions": active_transactions,
            "in_dispute": in_dispute,
            "completed": completed,
        }

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
            response.data["stats"] = stats
            return response

        serializer = self.get_serializer(queryset, many=True)
        return Response({"stats": stats, "results": serializer.data})


class KYCSubmissionListView(generics.ListAPIView):
    """
    List all KYC submissions with pagination and search filter.
    GET /administration/kyc-requests/?q=<search>&status=<status>
    """
    permission_classes = [permissions.IsAdminUser]
    serializer_class = KYCSubmissionListSerializer

    def get_queryset(self):
        queryset = KYCSubmission.objects.select_related("user").order_by("-submitted_at")
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
    serializer_class = KYCStatusUpdateSerializer
    queryset = KYCSubmission.objects.all()
    http_method_names = ["patch"]


class EscrowDetailPageView(APIView):
    permission_classes = [permissions.IsAdminUser]

    timeline_order = [
        Escrow.Status.CREATED,
        Escrow.Status.FUNDED,
        Escrow.Status.ACCEPTED,
        Escrow.Status.IN_PROGRESS,
        Escrow.Status.SHIPPED,
        Escrow.Status.DELIVERED,
        Escrow.Status.UNDER_REVIEW,
        Escrow.Status.ISSUE_RAISED,
        Escrow.Status.RETURN_IN_PROGRESS,
        Escrow.Status.RESOLVED,
        Escrow.Status.REFUNDED,
        Escrow.Status.PAYMENT_RELEASED,
        Escrow.Status.COMPLETED,
        Escrow.Status.CANCELLED,
    ]

    def get(self, request, pk):
        escrow = self.get_object(pk)
        if not escrow:
            return Response({"detail": "Escrow not found."}, status=status.HTTP_404_NOT_FOUND)

        payload = self.build_payload(escrow)
        serializer = EscrowDetailPageSerializer(payload)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def get_object(self, pk):
        try:
            return (
                Escrow.objects.select_related("created_by", "receiver")
                .prefetch_related("status_history", "installments")
                .get(pk=pk)
            )
        except Escrow.DoesNotExist:
            return None

    def resolve_party(self, escrow, is_seller):
        if escrow.role == Escrow.Role.SELLER:
            user = escrow.created_by if is_seller else escrow.receiver
        else:
            user = escrow.receiver if is_seller else escrow.created_by

        if not user:
            return {
                "label": "Seller" if is_seller else "Buyer",
                "name": "N/A",
                "email": None,
                "role": "seller" if is_seller else "buyer",
            }

        return {
            "label": "Seller" if is_seller else "Buyer",
            "name": user.full_name or user.username or user.email,
            "email": user.email,
            "role": "seller" if is_seller else "buyer",
        }

    def build_timeline(self, escrow):
        history_by_status = {item.status: item for item in escrow.status_history.all()}
        current_status = escrow.status
        timeline = []

        for status_value in self.timeline_order:
            history_item = history_by_status.get(status_value)
            timeline.append(
                {
                    "label": dict(Escrow.Status.choices).get(status_value, status_value.replace("_", " ").title()),
                    "status": status_value,
                    "timestamp": history_item.created_at if history_item else None,
                    "is_current": status_value == current_status,
                }
            )

        while timeline and timeline[-1]["timestamp"] is None and timeline[-1]["status"] not in (current_status, Escrow.Status.CREATED):
            timeline.pop()

        return timeline

    def build_inspection_period(self, escrow):
        delivered_entry = escrow.status_history.filter(status=Escrow.Status.DELIVERED).order_by("created_at").first()
        if not delivered_entry:
            return {
                "title": "Inspection Period",
                "value": "Not started",
                "deadline": None,
                "remaining_minutes": None,
                "is_active": False,
            }

        deadline = delivered_entry.created_at + timedelta(hours=24)
        now = timezone.now()
        remaining = deadline - now
        remaining_minutes = max(int(remaining.total_seconds() // 60), 0)
        hours, minutes = divmod(remaining_minutes, 60)

        return {
            "title": "Inspection Period",
            "value": f"{hours}h {minutes:02d}m",
            "deadline": deadline,
            "remaining_minutes": remaining_minutes,
            "is_active": remaining.total_seconds() > 0,
        }

    def build_fee_breakdown(self, escrow):
        fee_config = FeeConfiguration.objects.first()
        platform_fee_percentage = fee_config.stripe_fee_percentage if fee_config and fee_config.stripe_fee_percentage is not None else Decimal("2.5")
        transaction_amount = escrow.price if escrow.price is not None else sum((installment.amount for installment in escrow.installments.all()), Decimal("0.00"))
        platform_fee = (transaction_amount * platform_fee_percentage / Decimal("100")).quantize(Decimal("0.01"))
        escrow_fee = (escrow.fee_amount or Decimal("0.00")).quantize(Decimal("0.01"))
        total = escrow.total_amount if escrow.total_amount is not None else (transaction_amount + platform_fee + escrow_fee)

        return {
            "transaction_amount": transaction_amount,
            "platform_fee_label": f"Platform Fee {platform_fee_percentage}%",
            "platform_fee": platform_fee,
            "escrow_fee": escrow_fee,
            "total": total,
        }

    def build_admin_actions(self, escrow):
        active = escrow.status not in [Escrow.Status.COMPLETED, Escrow.Status.CANCELLED, Escrow.Status.REFUNDED]
        refund_enabled = escrow.status in [Escrow.Status.ISSUE_RAISED, Escrow.Status.UNDER_REVIEW, Escrow.Status.DELIVERED]

        return [
            {
                "action": "pause_escrow",
                "label": "Pause Escrow",
                "enabled": active,
                "message": None if active else "Escrow can no longer be paused.",
            },
            {
                "action": "refund_buyer",
                "label": "Refund Buyer",
                "enabled": refund_enabled,
                "message": None if refund_enabled else "Refund is only available during a dispute or after delivery.",
            },
        ]

    def build_payload(self, escrow):
        return {
            "item_name": escrow.product_name,
            "transaction_id": escrow.order_id,
            "status": escrow.status,
            "status_label": escrow.get_status_display(),
            "seller": self.resolve_party(escrow, is_seller=True),
            "buyer": self.resolve_party(escrow, is_seller=False),
            "timeline": self.build_timeline(escrow),
            "inspection_period": self.build_inspection_period(escrow),
            "fee_breakdown": self.build_fee_breakdown(escrow),
            "admin_actions": self.build_admin_actions(escrow),
        }


class AdminProfilePageView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        user = request.user
        
        name = user.full_name or user.username or user.email or "Admin"
        initials = "".join([part[0] for part in name.split()[:2]]).upper()
        
        payload = {
            "profile_photo": {
                "initials": initials,
                "url": request.build_absolute_uri(user.profile_pic.url) if getattr(user, 'profile_pic', None) else None
            },
            "name": name,
            "email": user.email,   
        }
        
        serializer = AdminProfilePageSerializer(payload)
        return Response(serializer.data, status=status.HTTP_200_OK)


class AdminProfileUpdateView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def patch(self, request):
        serializer = AdminProfileUpdateSerializer(request.user, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response({"detail": "Profile updated successfully."}, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AdminPasswordUpdateView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request):
        serializer = AdminPasswordUpdateSerializer(data=request.data)
        if serializer.is_valid():
            user = request.user
            if not user.check_password(serializer.validated_data["current_password"]):
                return Response({"current_password": ["Incorrect password."]}, status=status.HTTP_400_BAD_REQUEST)
            
            user.set_password(serializer.validated_data["new_password"])
            user.save()
            return Response({"detail": "Password updated successfully."}, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AdminWithdrawRequestListView(generics.ListAPIView):
    """
    GET — List all user withdraw requests (bank and paypal) with search and filters.
    Only accessible by administrators.
    """
    permission_classes = [permissions.IsAdminUser]
    serializer_class = AdminWithdrawRequestListSerializer
    pagination_class = PageNumberPagination

    def get_queryset(self):
        from app.profile.models import WithdrawTransaction
        queryset = WithdrawTransaction.objects.select_related("user").order_by("-created_at")

        # Status filter
        status_filter = self.request.query_params.get("status", "").strip().lower()
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        # Method filter
        method_filter = self.request.query_params.get("method", "").strip().lower()
        if method_filter:
            queryset = queryset.filter(method=method_filter)

        # Search parameter (username, user email, transaction_ref)
        search_query = self.request.query_params.get("q", "").strip()
        if search_query:
            from django.db.models import Q
            queryset = queryset.filter(
                Q(user__full_name__icontains=search_query)
                | Q(user__email__icontains=search_query)
                | Q(user__username__icontains=search_query)
                | Q(transaction_ref__icontains=search_query)
            )

        return queryset


class AdminWithdrawRequestStatusUpdateView(APIView):
    """
    PATCH — Approve or Reject a user withdraw request.
    Only accessible by administrators.
    """
    permission_classes = [permissions.IsAdminUser]

    def patch(self, request, pk):
        from app.profile.models import WithdrawTransaction, Wallet, WalletTransaction
        from django.db import transaction as db_transaction

        try:
            withdraw_txn = WithdrawTransaction.objects.select_related("user").get(pk=pk)
        except WithdrawTransaction.DoesNotExist:
            return Response({"error": "Withdraw request not found."}, status=status.HTTP_404_NOT_FOUND)

        if withdraw_txn.status != WithdrawTransaction.Status.PENDING:
            return Response(
                {"error": f"Withdraw request is not pending. Current status: {withdraw_txn.status}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        new_status = request.data.get("status", "").strip().lower()
        rejection_reason = request.data.get("rejection_reason", "").strip()

        if new_status not in ("completed", "failed"):
            return Response(
                {"error": "status must be 'completed' or 'failed'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with db_transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(user=withdraw_txn.user)

            if new_status == "completed":
                withdraw_txn.status = WithdrawTransaction.Status.COMPLETED
                withdraw_txn.description = "Withdrawal request approved and processed."
                withdraw_txn.save(update_fields=["status", "description", "updated_at"])

                # Log wallet transaction for withdrawal completion
                WalletTransaction.objects.create(
                    wallet=wallet,
                    transaction_type=WalletTransaction.TransactionType.WITHDRAWAL,
                    amount=withdraw_txn.amount,
                    fee=withdraw_txn.fee,
                    total_charged=withdraw_txn.amount,
                    status=WalletTransaction.Status.COMPLETED,
                    description=f"Withdrawal completed: {withdraw_txn.description}",
                )
            else:
                # Rejection/Failure: refund back to wallet atomically
                wallet.balance += withdraw_txn.amount
                wallet.save(update_fields=["balance", "updated_at"])

                withdraw_txn.status = WithdrawTransaction.Status.FAILED
                withdraw_txn.description = f"Withdrawal request rejected. {rejection_reason}".strip()
                withdraw_txn.save(update_fields=["status", "description", "updated_at"])

                # Log wallet transaction for withdrawal failure
                WalletTransaction.objects.create(
                    wallet=wallet,
                    transaction_type=WalletTransaction.TransactionType.WITHDRAWAL,
                    amount=withdraw_txn.amount,
                    fee=withdraw_txn.fee,
                    total_charged=withdraw_txn.amount,
                    status=WalletTransaction.Status.FAILED,
                    description=withdraw_txn.description,
                )

        return Response(
            {
                "success": True,
                "status": withdraw_txn.status,
                "description": withdraw_txn.description,
                "message": (
                    "Withdrawal request completed successfully."
                    if new_status == "completed"
                    else "Withdrawal request rejected and balance refunded."
                ),
            },
            status=status.HTTP_200_OK,
        )
