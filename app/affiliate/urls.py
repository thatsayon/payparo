from django.urls import path
from .views import (
    AffiliateClickTrackView,
    AffiliateApplicationView,
    AffiliatePortalDashboardView,
    AffiliateReferralLinkView,
    AffiliateRewardListView,
    AffiliateReferredUsersView,
    AffiliateWithdrawalListCreateView,
    AffiliateTierView,
    # Admin views
    AdminAffiliateListView,
    AdminAffiliateDetailView,
    AdminAffiliateStatusUpdateView,
    AdminAffiliateNoteView,
    AdminAffiliateWithdrawalListView,
    AdminAffiliateWithdrawalUpdateView,
    AdminAffiliateGlobalBudgetView,
    AdminAffiliateFraudListView,
    AdminAffiliateFraudResolveView,
)

# ── Affiliate-facing ──────────────────────────────────────────────────────────
affiliate_urlpatterns = [
    path("apply/", AffiliateApplicationView.as_view(), name="affiliate-apply"),
    path("dashboard/", AffiliatePortalDashboardView.as_view(), name="affiliate-dashboard"),
    path("link/", AffiliateReferralLinkView.as_view(), name="affiliate-link"),
    path("rewards/", AffiliateRewardListView.as_view(), name="affiliate-rewards"),
    path("referrals/", AffiliateReferredUsersView.as_view(), name="affiliate-referrals"),
    path("withdrawals/", AffiliateWithdrawalListCreateView.as_view(), name="affiliate-withdrawals"),
    path("tier/", AffiliateTierView.as_view(), name="affiliate-tier"),
]

# ── Admin-facing ──────────────────────────────────────────────────────────────
admin_affiliate_urlpatterns = [
    path("affiliates/", AdminAffiliateListView.as_view(), name="admin-affiliate-list"),
    path("affiliates/<uuid:pk>/", AdminAffiliateDetailView.as_view(), name="admin-affiliate-detail"),
    path("affiliates/<uuid:pk>/status/", AdminAffiliateStatusUpdateView.as_view(), name="admin-affiliate-status"),
    path("affiliates/<uuid:pk>/notes/", AdminAffiliateNoteView.as_view(), name="admin-affiliate-notes"),
    path("affiliates/withdrawals/", AdminAffiliateWithdrawalListView.as_view(), name="admin-affiliate-withdrawal-list"),
    path("affiliates/withdrawals/<uuid:pk>/status/", AdminAffiliateWithdrawalUpdateView.as_view(), name="admin-affiliate-withdrawal-status"),
    path("affiliates/budget/", AdminAffiliateGlobalBudgetView.as_view(), name="admin-affiliate-budget"),
    path("affiliates/fraud/", AdminAffiliateFraudListView.as_view(), name="admin-affiliate-fraud-list"),
    path("affiliates/fraud/<uuid:pk>/resolve/", AdminAffiliateFraudResolveView.as_view(), name="admin-affiliate-fraud-resolve"),
]

# ── Combined for affiliate app ────────────────────────────────────────────────
urlpatterns = affiliate_urlpatterns
