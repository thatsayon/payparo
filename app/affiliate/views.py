"""
Affiliate views — two sections:

1. Affiliate-facing: AffiliateApplicationView, portal dashboard, rewards, withdrawals, tier
2. Admin-facing: application management, payout approval, budget cap, fraud
3. Public: click tracking redirect
"""
from decimal import Decimal
from django.utils import timezone
from django.db import transaction as db_transaction
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404

from rest_framework import generics, permissions, status
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination

from .models import (
    AffiliateApplication,
    AffiliateProfile,
    AffiliateClick,
    AffiliateAttribution,
    AffiliateReward,
    AffiliateWithdrawal,
    AffiliateTierHistory,
    AffiliateGlobalBudget,
    AffiliateNote,
    AffiliateFraudFlag,
)
from .permissions import IsAffiliate
from .serializers import (
    AffiliateApplicationSerializer,
    AffiliateApplicationCreateSerializer,
    AdminAffiliateApplicationSerializer,
    AffiliateStatusUpdateSerializer,
    AffiliateProfileSerializer,
    AffiliateRewardSerializer,
    AffiliateAttributionSerializer,
    AffiliateWithdrawalSerializer,
    AffiliateWithdrawalCreateSerializer,
    AffiliateTierHistorySerializer,
    AffiliateGlobalBudgetSerializer,
    AffiliateGlobalBudgetUpdateSerializer,
    AffiliateNoteSerializer,
    AffiliateFraudFlagSerializer,
    AdminAffiliateWithdrawalSerializer,
    AdminWithdrawalStatusUpdateSerializer,
)
from .utils import PAYOUT_MINIMUM, detect_fraud_signals


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC — Click Tracking & Redirect
# ─────────────────────────────────────────────────────────────────────────────

class AffiliateClickTrackView(APIView):
    """
    GET /p/<slug>/
    Logs the click, sets a 30-day attribution cookie, and redirects to signup.
    """
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    SIGNUP_URL = "https://payparo.com/register"

    def get(self, request, slug):
        try:
            profile = AffiliateProfile.objects.get(slug__iexact=slug, is_active=True)
        except AffiliateProfile.DoesNotExist:
            return HttpResponseRedirect(self.SIGNUP_URL)

        ip = self._get_client_ip(request)
        ua = request.META.get("HTTP_USER_AGENT", "")
        fingerprint = request.GET.get("fp", "")

        click = AffiliateClick.objects.create(
            affiliate=profile,
            ip_address=ip,
            user_agent=ua,
            device_fingerprint=fingerprint,
        )

        redirect_url = f"{self.SIGNUP_URL}?ref={profile.affiliate_id}"
        response = HttpResponseRedirect(redirect_url)

        # Attribution cookie — 30 days
        response.set_cookie(
            "pp_aff",
            f"{profile.affiliate_id}:{click.pk}",
            max_age=60 * 60 * 24 * 30,
            httponly=True,
            samesite="Lax",
        )
        return response

    def _get_client_ip(self, request):
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        if xff:
            return xff.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC — Apply to become an affiliate
# ─────────────────────────────────────────────────────────────────────────────

class AffiliateApplicationView(APIView):
    """
    POST /api/affiliate/apply/ — Submit an affiliate application (any user).
    GET  /api/affiliate/apply/ — Check application status (authenticated, own application only).
    """
    permission_classes = [permissions.AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        serializer = AffiliateApplicationCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"error": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data.get("email", "")
        if AffiliateApplication.objects.filter(email__iexact=email).exists():
            return Response(
                {"error": "An application with this email already exists."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        application = serializer.save()
        if request.user.is_authenticated:
            application.user = request.user
            application.save(update_fields=["user"])

        return Response(
            {"success": True, "message": "Application submitted. Our team will review within 3-5 business days.", "id": str(application.id)},
            status=status.HTTP_201_CREATED,
        )

    def get(self, request):
        if not request.user.is_authenticated:
            return Response({"error": "Authentication required."}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            app = AffiliateApplication.objects.get(user=request.user)
        except AffiliateApplication.DoesNotExist:
            return Response({"status": "not_applied"}, status=status.HTTP_200_OK)
        return Response(AffiliateApplicationSerializer(app).data)


# ─────────────────────────────────────────────────────────────────────────────
# AFFILIATE — Portal Dashboard
# ─────────────────────────────────────────────────────────────────────────────

class AffiliatePortalDashboardView(APIView):
    """
    GET /api/affiliate/dashboard/
    Returns all key stats for the affiliate dashboard.
    """
    permission_classes = [IsAffiliate]

    def get(self, request):
        profile = get_object_or_404(AffiliateProfile, user=request.user)

        # Monthly volume (current month)
        from app.excrow.models import Escrow
        from django.db.models import Sum, Q
        now = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        attributed_ids = AffiliateAttribution.objects.filter(
            affiliate=profile
        ).values_list("referred_user_id", flat=True)

        monthly_volume = Escrow.objects.filter(
            Q(created_by_id__in=attributed_ids) | Q(receiver_id__in=attributed_ids),
            status=Escrow.Status.COMPLETED,
            created_at__gte=month_start,
        ).aggregate(total=Sum("price"))["total"] or Decimal("0.00")

        # Tier
        from .utils import TIER_VOLUME_THRESHOLD
        tier_progress_pct = min(100, float(monthly_volume / TIER_VOLUME_THRESHOLD * 100))

        # Recent rewards (last 5)
        recent_rewards = AffiliateReward.objects.filter(affiliate=profile).order_by("-created_at")[:5]

        return Response({
            "profile": AffiliateProfileSerializer(profile).data,
            "monthly_volume": str(monthly_volume),
            "tier_progress_pct": round(tier_progress_pct, 2),
            "referred_users_count": AffiliateAttribution.objects.filter(affiliate=profile).count(),
            "recent_rewards": AffiliateRewardSerializer(recent_rewards, many=True).data,
        })


# ─────────────────────────────────────────────────────────────────────────────
# AFFILIATE — Referral Link
# ─────────────────────────────────────────────────────────────────────────────

class AffiliateReferralLinkView(APIView):
    permission_classes = [IsAffiliate]

    def get(self, request):
        profile = get_object_or_404(AffiliateProfile, user=request.user)
        total_clicks = AffiliateClick.objects.filter(affiliate=profile).count()
        converted_clicks = AffiliateClick.objects.filter(affiliate=profile, converted=True).count()
        return Response({
            "slug": profile.slug,
            "affiliate_id": profile.affiliate_id,
            "referral_url": profile.referral_url,
            "total_clicks": total_clicks,
            "converted_clicks": converted_clicks,
            "conversion_rate": round(converted_clicks / total_clicks * 100, 2) if total_clicks else 0,
        })


# ─────────────────────────────────────────────────────────────────────────────
# AFFILIATE — Rewards Ledger
# ─────────────────────────────────────────────────────────────────────────────

class AffiliateRewardListView(generics.ListAPIView):
    permission_classes = [IsAffiliate]
    serializer_class = AffiliateRewardSerializer
    pagination_class = PageNumberPagination

    def get_queryset(self):
        profile = get_object_or_404(AffiliateProfile, user=self.request.user)
        qs = AffiliateReward.objects.filter(affiliate=profile).order_by("-created_at")
        state = self.request.query_params.get("state", "").strip()
        if state:
            qs = qs.filter(state=state)
        reward_type = self.request.query_params.get("type", "").strip()
        if reward_type:
            qs = qs.filter(reward_type=reward_type)
        return qs


# ─────────────────────────────────────────────────────────────────────────────
# AFFILIATE — Referred Users
# ─────────────────────────────────────────────────────────────────────────────

class AffiliateReferredUsersView(generics.ListAPIView):
    permission_classes = [IsAffiliate]
    serializer_class = AffiliateAttributionSerializer
    pagination_class = PageNumberPagination

    def get_queryset(self):
        profile = get_object_or_404(AffiliateProfile, user=self.request.user)
        return AffiliateAttribution.objects.filter(affiliate=profile).select_related("referred_user").order_by("-attributed_at")


# ─────────────────────────────────────────────────────────────────────────────
# AFFILIATE — Withdrawals
# ─────────────────────────────────────────────────────────────────────────────

class AffiliateWithdrawalListCreateView(APIView):
    permission_classes = [IsAffiliate]
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request):
        profile = get_object_or_404(AffiliateProfile, user=request.user)
        withdrawals = AffiliateWithdrawal.objects.filter(affiliate=profile).order_by("-created_at")
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(withdrawals, request)
        if page is not None:
            return paginator.get_paginated_response(AffiliateWithdrawalSerializer(page, many=True).data)
        return Response(AffiliateWithdrawalSerializer(withdrawals, many=True).data)

    def post(self, request):
        profile = get_object_or_404(AffiliateProfile, user=request.user)

        # Check pending withdrawal already exists
        if AffiliateWithdrawal.objects.filter(affiliate=profile, status=AffiliateWithdrawal.Status.PENDING).exists():
            return Response({"error": "You already have a pending withdrawal request."}, status=status.HTTP_400_BAD_REQUEST)

        if profile.withdrawable_balance < PAYOUT_MINIMUM:
            return Response(
                {"error": f"Minimum withdrawal amount is {PAYOUT_MINIMUM} MXN. Your current balance is {profile.withdrawable_balance} MXN."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = AffiliateWithdrawalCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"error": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        amount = serializer.validated_data["amount"]
        if amount > profile.withdrawable_balance:
            return Response({"error": "Insufficient withdrawable balance."}, status=status.HTTP_400_BAD_REQUEST)

        with db_transaction.atomic():
            withdrawal = serializer.save(affiliate=profile)
            # Freeze the balance
            profile_locked = AffiliateProfile.objects.select_for_update().get(pk=profile.pk)
            profile_locked.withdrawable_balance -= amount
            profile_locked.save(update_fields=["withdrawable_balance", "updated_at"])

        return Response(
            {"success": True, "message": "Withdrawal request submitted.", "id": str(withdrawal.id)},
            status=status.HTTP_201_CREATED,
        )


# ─────────────────────────────────────────────────────────────────────────────
# AFFILIATE — Tier
# ─────────────────────────────────────────────────────────────────────────────

class AffiliateTierView(APIView):
    permission_classes = [IsAffiliate]

    def get(self, request):
        profile = get_object_or_404(AffiliateProfile, user=request.user)
        history = AffiliateTierHistory.objects.filter(affiliate=profile).order_by("-year", "-month")[:12]

        from app.excrow.models import Escrow
        from django.db.models import Sum, Q
        now = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        attributed_ids = AffiliateAttribution.objects.filter(affiliate=profile).values_list("referred_user_id", flat=True)
        monthly_volume = Escrow.objects.filter(
            Q(created_by_id__in=attributed_ids) | Q(receiver_id__in=attributed_ids),
            status=Escrow.Status.COMPLETED,
            created_at__gte=month_start,
        ).aggregate(total=Sum("price"))["total"] or Decimal("0.00")

        from .utils import TIER_VOLUME_THRESHOLD, TIER_RATES
        return Response({
            "current_tier": profile.tier,
            "current_rate": str(TIER_RATES.get(profile.tier, Decimal("0.30"))),
            "monthly_volume": str(monthly_volume),
            "tier_threshold": str(TIER_VOLUME_THRESHOLD),
            "tier_progress_pct": round(min(100, float(monthly_volume / TIER_VOLUME_THRESHOLD * 100)), 2),
            "history": AffiliateTierHistorySerializer(history, many=True).data,
        })


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN — Affiliate Application List
# ─────────────────────────────────────────────────────────────────────────────

class AdminAffiliateListView(generics.ListAPIView):
    permission_classes = [permissions.IsAdminUser]
    serializer_class = AdminAffiliateApplicationSerializer
    pagination_class = PageNumberPagination

    def get_queryset(self):
        from django.db.models import Q
        qs = AffiliateApplication.objects.select_related("user", "reviewed_by").order_by("-created_at")
        status_filter = self.request.query_params.get("status", "").strip()
        if status_filter:
            qs = qs.filter(status=status_filter)
        q = self.request.query_params.get("q", "").strip()
        if q:
            qs = qs.filter(Q(full_name__icontains=q) | Q(email__icontains=q) | Q(community_name__icontains=q))
        return qs


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN — Affiliate Application Detail + Status Update
# ─────────────────────────────────────────────────────────────────────────────

class AdminAffiliateDetailView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request, pk):
        app = get_object_or_404(AffiliateApplication, pk=pk)
        return Response(AdminAffiliateApplicationSerializer(app).data)


class AdminAffiliateStatusUpdateView(APIView):
    """
    PATCH /api/administration/affiliates/<pk>/status/
    Approve (creates UserAccount + AffiliateProfile), reject, or suspend.
    """
    permission_classes = [permissions.IsAdminUser]

    def patch(self, request, pk):
        app = get_object_or_404(AffiliateApplication, pk=pk)
        serializer = AffiliateStatusUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        new_status = serializer.validated_data["status"]
        slug = serializer.validated_data.get("slug", "").strip()
        rejection_reason = serializer.validated_data.get("rejection_reason", "")

        if new_status == AffiliateApplication.Status.APPROVED:
            if not slug:
                return Response({"error": "A vanity slug is required to approve an affiliate."}, status=status.HTTP_400_BAD_REQUEST)
            if AffiliateProfile.objects.filter(slug__iexact=slug).exists():
                return Response({"error": "This slug is already taken."}, status=status.HTTP_400_BAD_REQUEST)
            return self._approve(request, app, slug)

        elif new_status == AffiliateApplication.Status.REJECTED:
            app.status = AffiliateApplication.Status.REJECTED
            app.rejection_reason = rejection_reason
            app.reviewed_by = request.user
            app.reviewed_at = timezone.now()
            app.save()
            return Response({"success": True, "status": app.status})

        elif new_status == AffiliateApplication.Status.SUSPENDED:
            app.status = AffiliateApplication.Status.SUSPENDED
            app.reviewed_by = request.user
            app.reviewed_at = timezone.now()
            app.save()
            if hasattr(app, "profile") and app.profile:
                app.profile.is_active = False
                app.profile.save(update_fields=["is_active"])
            return Response({"success": True, "status": app.status})

        return Response({"error": "Invalid status transition."}, status=status.HTTP_400_BAD_REQUEST)

    def _approve(self, request, app, slug):
        from django.contrib.auth import get_user_model
        from app.accounts.models import UserAccount

        User = get_user_model()

        with db_transaction.atomic():
            # Create or find user account for this affiliate
            if app.user:
                user = app.user
                user.role = UserAccount.Role.AFFILIATE
                user.save(update_fields=["role"])
            else:
                user = User(
                    email=app.email,
                    full_name=app.full_name,
                    role=UserAccount.Role.AFFILIATE,
                    is_active=True,
                )
                if app.password_hash:
                    user.password = app.password_hash
                else:
                    import uuid as _uuid
                    user.set_password(_uuid.uuid4().hex)
                user.save()
                app.user = user
                app.save(update_fields=["user"])

            # Create affiliate profile
            profile, created = AffiliateProfile.objects.get_or_create(
                user=user,
                defaults={"slug": slug, "application": app},
            )
            if not created:
                profile.slug = slug
                profile.is_active = True
                profile.save(update_fields=["slug", "is_active"])

            app.status = AffiliateApplication.Status.APPROVED
            app.reviewed_by = request.user
            app.reviewed_at = timezone.now()
            app.save()

        return Response({
            "success": True,
            "status": app.status,
            "affiliate_id": profile.affiliate_id,
            "slug": profile.slug,
            "user_id": str(user.id),
        })


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN — Notes
# ─────────────────────────────────────────────────────────────────────────────

class AdminAffiliateNoteView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, pk):
        app = get_object_or_404(AffiliateApplication, pk=pk)
        content = request.data.get("content", "").strip()
        if not content:
            return Response({"error": "Note content is required."}, status=status.HTTP_400_BAD_REQUEST)
        note = AffiliateNote.objects.create(application=app, author=request.user, content=content)
        return Response(AffiliateNoteSerializer(note).data, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN — Withdrawal Management
# ─────────────────────────────────────────────────────────────────────────────

class AdminAffiliateWithdrawalListView(generics.ListAPIView):
    permission_classes = [permissions.IsAdminUser]
    serializer_class = AdminAffiliateWithdrawalSerializer
    pagination_class = PageNumberPagination

    def get_queryset(self):
        from django.db.models import Q
        qs = AffiliateWithdrawal.objects.select_related("affiliate__user", "reviewed_by").order_by("-created_at")
        status_filter = self.request.query_params.get("status", "").strip()
        if status_filter:
            qs = qs.filter(status=status_filter)
        q = self.request.query_params.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(affiliate__user__email__icontains=q) |
                Q(affiliate__slug__icontains=q) |
                Q(transaction_ref__icontains=q)
            )
        return qs


class AdminAffiliateWithdrawalUpdateView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def patch(self, request, pk):
        withdrawal = get_object_or_404(AffiliateWithdrawal, pk=pk)

        if withdrawal.status not in (AffiliateWithdrawal.Status.PENDING, AffiliateWithdrawal.Status.APPROVED):
            return Response(
                {"error": f"Cannot update withdrawal in '{withdrawal.status}' state."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = AdminWithdrawalStatusUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        new_status = serializer.validated_data["status"]

        with db_transaction.atomic():
            withdrawal.reviewed_by = request.user
            withdrawal.reviewed_at = timezone.now()

            if new_status == "rejected":
                # Refund frozen balance
                profile = AffiliateProfile.objects.select_for_update().get(pk=withdrawal.affiliate.pk)
                profile.withdrawable_balance += withdrawal.amount
                profile.save(update_fields=["withdrawable_balance", "updated_at"])
                withdrawal.status = AffiliateWithdrawal.Status.REJECTED
                withdrawal.rejection_reason = serializer.validated_data.get("rejection_reason", "")

            elif new_status == "approved":
                isr = serializer.validated_data.get("isr_withholding", Decimal("0.00"))
                withdrawal.isr_withholding = isr
                withdrawal.net_amount = withdrawal.amount - isr
                withdrawal.status = AffiliateWithdrawal.Status.APPROVED

            elif new_status == "completed":
                withdrawal.status = AffiliateWithdrawal.Status.COMPLETED
                withdrawal.transaction_ref = serializer.validated_data.get("transaction_ref", "")
                # Update total_paid_out
                profile = AffiliateProfile.objects.select_for_update().get(pk=withdrawal.affiliate.pk)
                profile.total_paid_out += withdrawal.amount
                profile.total_released = max(Decimal("0.00"), profile.total_released - withdrawal.amount)
                profile.save(update_fields=["total_paid_out", "total_released", "updated_at"])

            withdrawal.admin_notes = serializer.validated_data.get("admin_notes", withdrawal.admin_notes)
            withdrawal.save()

        return Response({"success": True, "status": withdrawal.status})


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN — Global Budget Cap
# ─────────────────────────────────────────────────────────────────────────────

class AdminAffiliateGlobalBudgetView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        budget = AffiliateGlobalBudget.get_singleton()
        return Response(AffiliateGlobalBudgetSerializer(budget).data)

    def patch(self, request):
        budget = AffiliateGlobalBudget.get_singleton()
        serializer = AffiliateGlobalBudgetUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        if "monthly_cap" in serializer.validated_data:
            budget.monthly_cap = serializer.validated_data["monthly_cap"]
        if "rewards_paused" in serializer.validated_data:
            budget.rewards_paused = serializer.validated_data["rewards_paused"]
        budget.save()
        return Response(AffiliateGlobalBudgetSerializer(budget).data)


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN — Fraud Flags
# ─────────────────────────────────────────────────────────────────────────────

class AdminAffiliateFraudListView(generics.ListAPIView):
    permission_classes = [permissions.IsAdminUser]
    serializer_class = AffiliateFraudFlagSerializer
    pagination_class = PageNumberPagination

    def get_queryset(self):
        qs = AffiliateFraudFlag.objects.select_related("affiliate", "attributed_user").order_by("-created_at")
        resolved = self.request.query_params.get("resolved", "").strip()
        if resolved in ("true", "false"):
            qs = qs.filter(resolved=resolved == "true")
        return qs


class AdminAffiliateFraudResolveView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def patch(self, request, pk):
        flag = get_object_or_404(AffiliateFraudFlag, pk=pk)
        flag.resolved = True
        flag.resolved_by = request.user
        flag.resolved_at = timezone.now()
        flag.save()
        return Response({"success": True, "resolved": True})
