from django.urls import path
from .views import (
    EscrowListCreateView, 
    EscrowDetailView, 
    UserSearchView,
    OrderHistory,
    OrderHistoryDetailView,
    EscrowAcceptView,
)

urlpatterns = [
    path("search/", UserSearchView.as_view(), name="user-search"),
    path("", EscrowListCreateView.as_view(), name="escrow-list-create"),
    path("<uuid:pk>/", EscrowDetailView.as_view(), name="escrow-detail"),
    path("<uuid:pk>/accept/", EscrowAcceptView.as_view(), name="escrow-accept"),
    path("order-history/", OrderHistory.as_view(), name="order-history"),
    path("order-history/<uuid:pk>/", OrderHistoryDetailView.as_view(), name="order-history-detail"),
]
