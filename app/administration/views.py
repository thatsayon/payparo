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
    RevenueStatsSerializer,
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


class UserSuspendView(APIView):
    """
    PATCH /administration/users/<uuid:pk>/suspend/
    Toggles is_active on a user account.
    Body: {"suspend": true}  → suspend (is_active=False)
          {"suspend": false} → unsuspend (is_active=True)
    Returns the updated user object.
    """
    permission_classes = [permissions.IsAdminUser]

    def patch(self, request, pk, *args, **kwargs):
        try:
            user = UserAccount.objects.get(pk=pk)
        except UserAccount.DoesNotExist:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        # Prevent admins from suspending themselves or other admins
        if user.role in [UserAccount.Role.ADMIN, UserAccount.Role.KYC]:
            return Response(
                {"detail": "Cannot suspend an admin or KYC specialist account."},
                status=status.HTTP_403_FORBIDDEN,
            )

        suspend = request.data.get("suspend")
        if suspend is None:
            return Response({"detail": "Field 'suspend' (boolean) is required."}, status=status.HTTP_400_BAD_REQUEST)

        user.is_active = not bool(suspend)
        user.save(update_fields=["is_active"])

        action = "suspended" if suspend else "unsuspended"
        return Response(
            {"detail": f"User has been {action}.", "is_suspended": not user.is_active},
            status=status.HTTP_200_OK,
        )


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


class AdminRevenueView(APIView):
    """
    GET /api/administration/revenue/
    Returns real platform revenue derived from completed escrow fee_amount,
    monthly breakdown for the past 12 months, and recent completed escrows.
    """
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        from django.db.models import Sum
        from django.db.models.functions import TruncMonth
        from decimal import Decimal

        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=now.weekday())
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        completed_qs = Escrow.objects.filter(status=Escrow.Status.COMPLETED)

        # ── Revenue from platform fees on completed escrows ───────────────────
        total_revenue = completed_qs.aggregate(
            t=Sum("fee_amount")
        )["t"] or Decimal("0.00")

        today_revenue = completed_qs.filter(
            updated_at__gte=today_start
        ).aggregate(t=Sum("fee_amount"))["t"] or Decimal("0.00")

        week_revenue = completed_qs.filter(
            updated_at__gte=week_start
        ).aggregate(t=Sum("fee_amount"))["t"] or Decimal("0.00")

        month_revenue = completed_qs.filter(
            updated_at__gte=month_start
        ).aggregate(t=Sum("fee_amount"))["t"] or Decimal("0.00")

        # ── Total escrow volume (price of completed transactions) ─────────────
        total_volume = completed_qs.aggregate(
            v=Sum("total_amount")
        )["v"] or Decimal("0.00")

        # ── Counts ────────────────────────────────────────────────────────────
        total_completed = completed_qs.count()
        total_refunded = Escrow.objects.filter(status=Escrow.Status.REFUNDED).count()
        active_statuses = [
            Escrow.Status.FUNDED,
            Escrow.Status.ACCEPTED,
            Escrow.Status.IN_PROGRESS,
            Escrow.Status.SHIPPED,
            Escrow.Status.UNDER_REVIEW,
        ]
        total_active = Escrow.objects.filter(status__in=active_statuses).count()

        # ── Monthly revenue: last 12 months ───────────────────────────────────
        twelve_months_ago = now - timedelta(days=365)
        monthly_qs = (
            completed_qs
            .filter(updated_at__gte=twelve_months_ago)
            .annotate(month=TruncMonth("updated_at"))
            .values("month")
            .annotate(revenue=Sum("fee_amount"))
            .order_by("month")
        )
        monthly_revenue = [
            {
                "month": entry["month"].strftime("%b %Y"),
                "revenue": float(entry["revenue"] or 0),
            }
            for entry in monthly_qs
        ]

        # ── Recent 10 completed escrows ────────────────────────────────────────
        recent_qs = completed_qs.select_related("created_by", "receiver").order_by("-updated_at")[:10]
        recent_escrows = []
        for e in recent_qs:
            seller = None
            buyer = None
            if e.role == Escrow.Role.SELLER:
                seller = e.created_by
                buyer = e.receiver
            else:
                buyer = e.created_by
                seller = e.receiver

            recent_escrows.append({
                "order_id": e.order_id,
                "product_name": e.product_name,
                "total_amount": float(e.total_amount or e.price or 0),
                "fee_amount": float(e.fee_amount or 0),
                "currency": e.currency,
                "seller": seller.full_name or seller.email if seller else "N/A",
                "buyer": buyer.full_name or buyer.email if buyer else "N/A",
                "completed_at": e.updated_at.isoformat(),
            })

        payload = {
            "today_revenue": today_revenue,
            "this_week_revenue": week_revenue,
            "this_month_revenue": month_revenue,
            "total_revenue": total_revenue,
            "total_escrow_volume": total_volume,
            "total_completed_escrows": total_completed,
            "total_active_escrows": total_active,
            "total_refunded_escrows": total_refunded,
            "monthly_revenue": monthly_revenue,
            "recent_escrows": recent_escrows,
        }

        serializer = RevenueStatsSerializer(payload)
        return Response(serializer.data, status=status.HTTP_200_OK)


from django.db.models import Sum
from app.excrow.models import Escrow, EscrowDispute

class AdminDashboardOverviewView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        # 1. Stats Cards
        total_users = UserAccount.objects.count()
        pending_kyc = KYCSubmission.objects.filter(status__in=["pending", "under_review"]).count()
        
        active_escrows_qs = Escrow.objects.exclude(status__in=["completed", "cancelled", "refunded", "created"])
        active_escrow_volume = active_escrows_qs.aggregate(vol=Sum('price'))['vol'] or Decimal('0.00')
        
        open_disputes = EscrowDispute.objects.filter(status__in=["pending_ai", "awaiting_seller", "pending_kyc"]).count()
        
        # 2. Escrow Line Chart (Last 6 Months)
        now = timezone.now()
        months_data = []
        for i in range(5, -1, -1):
            start_date = (now - timedelta(days=i*30)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if start_date.month == 12:
                end_date = start_date.replace(year=start_date.year + 1, month=1)
            else:
                end_date = start_date.replace(month=start_date.month + 1)
                
            monthly_escrows = Escrow.objects.filter(created_at__gte=start_date, created_at__lt=end_date)
            escrow_count = monthly_escrows.count()
            escrow_vol = monthly_escrows.aggregate(vol=Sum('price'))['vol'] or Decimal('0.00')
            
            months_data.append({
                "month": start_date.strftime("%B"),
                "count": escrow_count,
                "volume": float(escrow_vol)
            })
            
        # 3. User Registration Bar Chart (Last 6 Months)
        user_reg_data = []
        for i in range(5, -1, -1):
            start_date = (now - timedelta(days=i*30)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if start_date.month == 12:
                end_date = start_date.replace(year=start_date.year + 1, month=1)
            else:
                end_date = start_date.replace(month=start_date.month + 1)
                
            monthly_users = UserAccount.objects.filter(created_at__gte=start_date, created_at__lt=end_date)
            user_reg_data.append({
                "month": start_date.strftime("%b"),
                "registrations": monthly_users.count()
            })
            
        # 4. Activity Feed (Merged and sorted timeline)
        activities = []
        
        # Recent User Signups
        recent_signups = UserAccount.objects.order_by('-created_at')[:5]
        for u in recent_signups:
            activities.append({
                "id": f"signup-{u.id}",
                "type": "signup",
                "title": "New User Registered",
                "description": f"{u.full_name or u.email} joined the platform.",
                "timestamp": u.created_at.isoformat()
            })
            
        # Recent Escrows
        recent_escrows = Escrow.objects.order_by('-created_at')[:5]
        for e in recent_escrows:
            activities.append({
                "id": f"escrow-{e.id}",
                "type": "escrow",
                "title": "Escrow Created",
                "description": f"Order {e.order_id} ({e.product_name}) created for {e.price} {e.currency}.",
                "timestamp": e.created_at.isoformat()
            })
            
        # Recent Disputes
        recent_disputes = EscrowDispute.objects.order_by('-created_at')[:5]
        for d in recent_disputes:
            activities.append({
                "id": f"dispute-{d.id}",
                "type": "dispute",
                "title": "Dispute Raised",
                "description": f"Dispute raised on order {d.escrow.order_id} ({d.reason}).",
                "timestamp": d.created_at.isoformat()
            })
            
        # Sort activities by timestamp desc and take top 10
        activities = sorted(activities, key=lambda x: x["timestamp"], reverse=True)[:10]
        
        response_data = {
            "stats": {
                "total_users": total_users,
                "pending_kyc": pending_kyc,
                "active_escrow_volume": float(active_escrow_volume),
                "open_disputes": open_disputes
            },
            "escrow_chart": months_data,
            "registration_chart": user_reg_data,
            "activity_feed": activities
        }
        
        return Response(response_data, status=status.HTTP_200_OK)


class MarketingBannerListCreateView(generics.ListCreateAPIView):
    from app.administration.models import MarketingBanner
    from .serializers import MarketingBannerSerializer

    queryset = MarketingBanner.objects.all().order_by("-created_at")
    serializer_class = MarketingBannerSerializer
    pagination_class = None

    def get_permissions(self):
        if self.request.method == "GET":
            return [permissions.IsAuthenticated()]
        return [permissions.IsAdminUser()]


class MarketingBannerDestroyView(generics.DestroyAPIView):
    from app.administration.models import MarketingBanner
    from .serializers import MarketingBannerSerializer

    queryset = MarketingBanner.objects.all()
    serializer_class = MarketingBannerSerializer
    permission_classes = [permissions.IsAdminUser]



