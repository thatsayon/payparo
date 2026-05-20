"""
URL configuration for config project.
"""
from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)
from app.affiliate.views import AffiliateClickTrackView
from app.affiliate.urls import admin_affiliate_urlpatterns

urlpatterns = [
    path('admin/', admin.site.urls),

    # Administration
    path('api/administration/', include('app.administration.urls')),

    # Auth
    path('api/auth/', include('app.accounts.urls')),

    # Messaging
    path('api/messaging/', include('app.messaging.urls')),

    # Escrow
    path('api/escrow/', include('app.excrow.urls')),

    # Profile & Wallet
    path('api/profile/', include('app.profile.urls')),

    # Refer & Earn (simple referral - legacy)
    path('api/refer/', include('app.refer.urls')),

    # Affiliate System
    path('api/affiliate/', include('app.affiliate.urls')),

    # Admin Affiliate Management (extends administration)
    path('api/administration/', include(admin_affiliate_urlpatterns)),

    # Affiliate Click Tracking & Redirect (public)
    path('p/<slug:slug>/', AffiliateClickTrackView.as_view(), name='affiliate-click'),

    # AI Dispute Analysis (staff only)
    path('api/ai/', include('app.ai.urls')),

    # KYC
    path('api/kyc/', include('app.kyc.urls')),

    # API Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    path('api/notifications/', include('app.notification.urls')),
]
