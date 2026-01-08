"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
"""
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)

from apps.markets.views import MarketViewSet, AssetViewSet, OHLCVViewSet
from apps.analytics.views import TechnicalIndicatorViewSet

# Create a router and register our viewsets
router = DefaultRouter()
router.register(r'markets', MarketViewSet, basename='market')
router.register(r'assets', AssetViewSet, basename='asset')
router.register(r'ohlcv', OHLCVViewSet, basename='ohlcv')
router.register(r'indicators', TechnicalIndicatorViewSet, basename='indicator')

urlpatterns = [
    path('admin/', admin.site.urls),
    # API v1 endpoints
    path('api/v1/', include(router.urls)),
    # JWT authentication
    path('api/v1/auth/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/v1/auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/v1/auth/token/verify/', TokenVerifyView.as_view(), name='token_verify'),
    # Browsable API authentication
    path('api-auth/', include('rest_framework.urls', namespace='rest_framework')),
]
