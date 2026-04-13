from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ReferralProfile, ReferralEarning
from .serializers import ReferredUserSerializer
from django.conf import settings


class ReferralDashboardView(APIView):
    """
    GET: Returns data for the Refer & Earn dashboard.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        
        # Ensure user has a profile (since it's auto-generated, it should exist, but get_or_create is safer just in case)
        profile, _ = ReferralProfile.objects.get_or_create(user=user)

        # 1. Total Earnings & Referral Code
        total_earnings = profile.total_earnings
        referral_code = profile.referral_code

        # 2. Total Referrals (Count of users with this user as 'referred_by' on their ReferralProfile)
        referrals_count = ReferralProfile.objects.filter(referred_by=user).count()

        # 3. Pending Commissions
        pending_count = ReferralEarning.objects.filter(
            referrer=user, 
            status=ReferralEarning.Status.PENDING
        ).count()

        # 4. List of Referred Users
        # Get all users whose ReferralProfile has 'referred_by' set to this user
        referred_profiles = ReferralProfile.objects.filter(referred_by=user).select_related('user').order_by('-created_at')
        
        # Extract the user objects
        referred_users = [pf.user for pf in referred_profiles]
        
        # Serialize their data
        referred_users_data = ReferredUserSerializer(referred_users, many=True).data

        return Response({
            "success": True,
            "referral_code": referral_code,
            "total_earnings": str(total_earnings),
            "referrals_count": referrals_count,
            "pending_count": pending_count,
            "referred_users": referred_users_data
        }, status=status.HTTP_200_OK)
