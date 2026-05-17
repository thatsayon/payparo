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

    # Refer & Earn
    path('api/refer/', include('app.refer.urls')),

    # AI Dispute Analysis (staff only)
    path('api/ai/', include('app.ai.urls')),

    # API Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]
