from rest_framework import generics, permissions, status
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from django.contrib.auth import get_user_model
from django.db import transaction

from .models import Invitation
import uuid

User = get_user_model()

class IsAdminUser(permissions.BasePermission):
    """Allows access only to admin users."""
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == User.Role.ADMIN)

class InviteKYCView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        email = request.data.get("email")
        role = request.data.get("role", User.Role.KYC)

        if not email:
            return Response({"error": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)
        
        email = email.lower().strip()

        if User.objects.filter(email=email).exists():
            return Response({"error": "User with this email already exists."}, status=status.HTTP_400_BAD_REQUEST)

        invitation, created = Invitation.objects.get_or_create(
            email=email,
            defaults={"role": role}
        )

        if not created:
            # Update token and role just in case
            invitation.token = uuid.uuid4()
            invitation.role = role
            invitation.save()

        # Build the invitation link and send the email
        base_url = request.headers.get("Origin") or f"{request.scheme}://{request.get_host()}"
        invitation_link = f"{base_url}/accept-invite?token={invitation.token}"

        from .tasks import send_kyc_invitation_email_task
        send_kyc_invitation_email_task.delay(email, invitation_link)

        return Response({
            "success": True,
            "message": "Invitation sent successfully."
        }, status=status.HTTP_201_CREATED)


class ResendInviteView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, id):
        # Try as a pending Invitation first
        invitation = Invitation.objects.filter(id=id, is_accepted=False).first()

        if invitation is None:
            # Check if this UUID belongs to an already-active KYC user
            if User.objects.filter(id=id, role=User.Role.KYC).exists():
                return Response(
                    {"error": "This user has already accepted their invitation and is active. No resend needed."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response({"error": "Invitation not found."}, status=status.HTTP_404_NOT_FOUND)

        invitation.token = uuid.uuid4()
        invitation.save()

        # Build the invitation link and resend the email
        base_url = request.headers.get("Origin") or f"{request.scheme}://{request.get_host()}"
        invitation_link = f"{base_url}/accept-invite?token={invitation.token}"

        from .tasks import send_kyc_invitation_email_task
        send_kyc_invitation_email_task.delay(invitation.email, invitation_link)

        return Response({
            "success": True,
            "message": "Invitation resent successfully."
        }, status=status.HTTP_200_OK)


class KYCTeamPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class ListKYCView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        # Active KYC specialists
        kyc_users = User.objects.filter(role=User.Role.KYC)
        team = []
        for u in kyc_users:
            team.append({
                "id": str(u.id),
                "email": u.email,
                "name": u.full_name or "N/A",
                "role": u.role,
                "status": "active",
                "issue_resolved_count": getattr(u, 'resolved_issues_count', 0)
            })

        # Pending invitations
        invitations = Invitation.objects.filter(is_accepted=False)
        for inv in invitations:
            team.append({
                "id": str(inv.id),   # Use directly in /admin/kyc/invite/<id>/resend/
                "email": inv.email,
                "name": "Pending...",
                "role": inv.role,
                "status": "pending",
                "issue_resolved_count": 0
            })

        paginator = KYCTeamPagination()
        page = paginator.paginate_queryset(team, request)
        return paginator.get_paginated_response(page)


class RemoveKYCView(APIView):
    permission_classes = [IsAdminUser]

    def delete(self, request, id):
        deleted_anything = False

        # If it's a pending invitation, delete it
        invitation = Invitation.objects.filter(id=id, is_accepted=False).first()
        if invitation:
            invitation.delete()
            deleted_anything = True

        # If it's an active KYC user, demote to USER
        if not deleted_anything:
            user = User.objects.filter(id=id, role=User.Role.KYC).first()
            if user:
                user.role = User.Role.USER
                user.save(update_fields=["role"])
                deleted_anything = True

        if not deleted_anything:
            return Response({"error": "No matching KYC specialist or invitation found."}, status=status.HTTP_404_NOT_FOUND)

        return Response({
            "success": True,
            "message": "KYC specialist/invitation removed successfully."
        }, status=status.HTTP_200_OK)


class VerifyInviteTokenView(APIView):
    """Check whether a KYC invitation token is valid (without consuming it)."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        token = request.data.get("token")
        if not token:
            return Response({"error": "Token is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            invitation = Invitation.objects.get(token=token, is_accepted=False)
        except Invitation.DoesNotExist:
            return Response({"error": "Invalid or expired invitation token."}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            "success": True,
            "email": invitation.email,
            "role": invitation.role,
        }, status=status.HTTP_200_OK)


class AcceptInviteView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        token = request.data.get("token")
        password = request.data.get("password")
        full_name = request.data.get("full_name")

        if not all([token, password, full_name]):
            return Response({"error": "Token, password, and full_name are required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            invitation = Invitation.objects.get(token=token, is_accepted=False)
        except Invitation.DoesNotExist:
            return Response({"error": "Invalid or expired invitation token."}, status=status.HTTP_400_BAD_REQUEST)

        if User.objects.filter(email=invitation.email).exists():
            return Response({"error": "A user with this email already exists."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            user = User.objects.create_user(
                email=invitation.email,
                password=password,
                full_name=full_name,
                role=invitation.role,
                is_active=True  # Automatically active since they were invited
            )
            invitation.is_accepted = True
            invitation.save()

        return Response({
            "success": True,
            "message": "Account created successfully. You can now log in."
        }, status=status.HTTP_201_CREATED)
