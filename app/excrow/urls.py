from django.urls import path
from .views import EscrowListCreateView, EscrowDetailView, UserSearchView

urlpatterns = [
    path("search/", UserSearchView.as_view(), name="user-search"),
    path("", EscrowListCreateView.as_view(), name="escrow-list-create"),
    path("<uuid:pk>/", EscrowDetailView.as_view(), name="escrow-detail"),
]
