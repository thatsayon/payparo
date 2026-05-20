from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone
from datetime import timedelta
import re

from .models import ReferralProfile, ReferralEarning
from .serializers import ReferredUserSerializer
from app.excrow.models import Escrow, EscrowStatusHistory
from .tasks import process_pending_referrals


class ReferralDashboardView(APIView):
    """
    GET: Returns data for the Refer & Earn dashboard.
    PUT/PATCH: Allows customizing the user's unique referral code.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        
        # 1. Process and clear any mature pending referrals for this user
        process_pending_referrals(referrer=user)
        
        # Ensure user has a profile
        profile, _ = ReferralProfile.objects.get_or_create(user=user)

        # 2. Total Earnings & Referral Code
        total_earnings = profile.total_earnings
        referral_code = profile.referral_code

        # 3. Total Referrals (Completed referrals)
        referrals_count = ReferralEarning.objects.filter(
            referrer=user, 
            status=ReferralEarning.Status.COMPLETED
        ).count()

        # 4. Pending Requests Count
        pending_count = ReferralEarning.objects.filter(
            referrer=user, 
            status=ReferralEarning.Status.PENDING
        ).count()

        # 5. List of referred users with detailed timestamps and escrow countdowns
        referred_profiles = ReferralProfile.objects.filter(referred_by=user).select_related('user').order_by('-referred_at')
        
        referred_users_data = []
        for pf in referred_profiles:
            referred_user = pf.user
            earning = ReferralEarning.objects.filter(referrer=user, referred_user=referred_user).first()
            
            earning_status = "pending"
            amount = ReferralProfile.REFERRAL_COMMISSION_AMOUNT
            time_left_hours = None
            escrow_id = None
            escrow_order_id = None
            escrow_status = None
            
            if earning:
                earning_status = earning.status
                amount = float(earning.amount)
                if earning.escrow:
                    escrow_id = str(earning.escrow.id)
                    escrow_order_id = earning.escrow.order_id
                    escrow_status = earning.escrow.status
                    
                    if earning.status == ReferralEarning.Status.PENDING and escrow_status in [
                        Escrow.Status.DELIVERED, Escrow.Status.COMPLETED, Escrow.Status.PAYMENT_RELEASED
                    ]:
                        delivery_history = EscrowStatusHistory.objects.filter(
                            escrow=earning.escrow,
                            status=Escrow.Status.DELIVERED
                        ).order_by('created_at').first()
                        
                        if delivery_history:
                            elapsed = timezone.now() - delivery_history.created_at
                            remaining = timedelta(hours=48) - elapsed
                            time_left_hours = max(0.0, remaining.total_seconds() / 3600.0)
            
            profile_pic_url = None
            if referred_user.profile_pic:
                if hasattr(referred_user.profile_pic, "url"):
                    profile_pic_url = referred_user.profile_pic.url
                else:
                    import cloudinary
                    profile_pic_url = cloudinary.CloudinaryImage(str(referred_user.profile_pic)).build_url()

            referred_users_data.append({
                "id": str(referred_user.id),
                "email": referred_user.email,
                "full_name": referred_user.full_name,
                "username": referred_user.username,
                "profile_pic_url": profile_pic_url,
                "date_joined": referred_user.date_joined,
                "referred_at": pf.referred_at or pf.created_at,
                "status": earning_status,
                "amount": amount,
                "time_left_hours": time_left_hours,
                "escrow_id": escrow_id,
                "escrow_order_id": escrow_order_id,
                "escrow_status": escrow_status
            })

        return Response({
            "success": True,
            "referral_code": referral_code,
            "total_earnings": str(total_earnings),
            "referrals_count": referrals_count,
            "pending_count": pending_count,
            "referred_users": referred_users_data
        }, status=status.HTTP_200_OK)

    def put(self, request):
        return self.update_code(request)

    def patch(self, request):
        return self.update_code(request)

    def update_code(self, request):
        user = request.user
        new_code = request.data.get("referral_code", "").strip()
        if not new_code:
            return Response({"error": "referral_code is required."}, status=status.HTTP_400_BAD_REQUEST)
        
        if len(new_code) < 3 or len(new_code) > 25:
            return Response({"error": "Referral code must be between 3 and 25 characters."}, status=status.HTTP_400_BAD_REQUEST)
            
        if not re.match(r"^[a-zA-Z0-9\-_]+$", new_code):
            return Response({"error": "Referral code can only contain alphanumeric characters, hyphens, and underscores."}, status=status.HTTP_400_BAD_REQUEST)
            
        profile, _ = ReferralProfile.objects.get_or_create(user=user)
        if ReferralProfile.objects.filter(referral_code__iexact=new_code).exclude(id=profile.id).exists():
            return Response({"error": "This referral code is already taken. Please choose another one."}, status=status.HTTP_400_BAD_REQUEST)
            
        profile.referral_code = new_code
        profile.save()
        
        return Response({
            "success": True,
            "message": "Referral code updated successfully.",
            "referral_code": profile.referral_code
        }, status=status.HTTP_200_OK)


class ReferralCodeApplyView(APIView):
    """
    POST: Apply a referral code to the user's account post-registration.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        referral_code = request.data.get("referral_code", "").strip()
        if not referral_code:
            return Response({"error": "referral_code is required."}, status=status.HTTP_400_BAD_REQUEST)
            
        profile, _ = ReferralProfile.objects.get_or_create(user=user)
        
        if profile.referred_by:
            return Response({"error": "You have already applied a referral code."}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            referrer_profile = ReferralProfile.objects.get(referral_code__iexact=referral_code)
        except ReferralProfile.DoesNotExist:
            return Response({"error": "Invalid referral code."}, status=status.HTTP_400_BAD_REQUEST)
            
        if referrer_profile.user == user:
            return Response({"error": "You cannot refer yourself."}, status=status.HTTP_400_BAD_REQUEST)
            
        from django.db import transaction as db_transaction
        with db_transaction.atomic():
            profile.referred_by = referrer_profile.user
            profile.referred_at = timezone.now()
            profile.save()
            
            ReferralEarning.objects.create(
                referrer=referrer_profile.user,
                referred_user=user,
                amount=ReferralProfile.REFERRAL_COMMISSION_AMOUNT,
                status=ReferralEarning.Status.PENDING
            )
            
        return Response({
            "success": True,
            "message": f"Referral code from {referrer_profile.user.email} successfully applied."
        }, status=status.HTTP_200_OK)
