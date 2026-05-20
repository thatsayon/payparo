from rest_framework.permissions import BasePermission


class IsAffiliate(BasePermission):
    """Allow access only to users with role='affiliate'."""

    message = "You must be an approved affiliate to access this resource."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and getattr(request.user, "role", None) == "affiliate"
        )
