from django.urls import path
from .views import (
    EscrowListCreateView, 
    EscrowDetailView, 
    UserSearchView,
    OrderHistory,
    OrderHistoryDetailView,
    EscrowAcceptView,
    EscrowSendProductView,
    EscrowDeliveredView,
    DisputeListView,
)

urlpatterns = [
    path("search/", UserSearchView.as_view(), name="user-search"),
    path("", EscrowListCreateView.as_view(), name="escrow-list-create"),
    path("<uuid:pk>/", EscrowDetailView.as_view(), name="escrow-detail"),
    path("<uuid:pk>/accept/", EscrowAcceptView.as_view(), name="escrow-accept"),
    path("<uuid:pk>/send-product/", EscrowSendProductView.as_view(), name="escrow-send-product"),
    path("<uuid:pk>/delivered/", EscrowDeliveredView.as_view(), name="escrow-delivered"),
    path("order-history/", OrderHistory.as_view(), name="order-history"),
    path("order-history/<uuid:pk>/", OrderHistoryDetailView.as_view(), name="order-history-detail"),
    path("disputes/", DisputeListView.as_view(), name="escrow-disputes"),
]
