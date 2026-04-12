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
from apps.analytics.views import (
    TechnicalIndicatorViewSet,
    ScreenerTemplateViewSet,
    ScreenerViewSet,
    AlertRuleViewSet,
    AlertEventViewSet,
    SignalEventViewSet,
)
from apps.users.views import (
    UserRegistrationView,
    EmailVerificationView,
    PasswordResetRequestView,
    PasswordResetConfirmView,
    UserProfileViewSet,
    SubscriptionViewSet,
    APIUsageViewSet,
)
from apps.factors.views import (
    FundamentalFactorSnapshotViewSet,
    CapitalFlowSnapshotViewSet,
    BottomCandidateViewSet,
)
from apps.macro.views import (
    MacroSnapshotViewSet,
    MarketContextViewSet,
    EventImpactStatViewSet,
)
from apps.sentiment.views import (
    NewsArticleViewSet,
    SentimentScoreViewSet,
    ConceptHeatViewSet,
)
from apps.prediction.views import (
    PredictionViewSet,
    ModelVersionViewSet,
)
from apps.prediction.views_lightgbm import (
    LightGBMPredictionViewSet,
    LightGBMModelArtifactViewSet,
    EnsembleWeightSnapshotViewSet,
)
from apps.backtest.views import (
    BacktestRunViewSet,
    BacktestTradeViewSet,
)

# Create a router and register our viewsets
router = DefaultRouter()
router.register(r'markets', MarketViewSet, basename='market')
router.register(r'assets', AssetViewSet, basename='asset')
router.register(r'ohlcv', OHLCVViewSet, basename='ohlcv')
router.register(r'indicators', TechnicalIndicatorViewSet, basename='indicator')
router.register(r'screener-templates', ScreenerTemplateViewSet, basename='screener-template')
router.register(r'screeners', ScreenerViewSet, basename='screener')
router.register(r'alerts', AlertRuleViewSet, basename='alert-rule')
router.register(r'alert-events', AlertEventViewSet, basename='alert-event')
router.register(r'signals', SignalEventViewSet, basename='signal-event')
router.register(r'users/profile', UserProfileViewSet, basename='userprofile')
router.register(r'users/subscriptions', SubscriptionViewSet, basename='subscription')
router.register(r'users/usage', APIUsageViewSet, basename='apiusage')
router.register(r'factors/fundamentals', FundamentalFactorSnapshotViewSet, basename='fundamental-factor')
router.register(r'factors/capital-flows', CapitalFlowSnapshotViewSet, basename='capital-flow-factor')
router.register(r'screener/bottom-candidates', BottomCandidateViewSet, basename='bottom-candidates')
router.register(r'macro/snapshots', MacroSnapshotViewSet, basename='macro-snapshot')
router.register(r'macro/contexts', MarketContextViewSet, basename='macro-context')
router.register(r'macro/event-impacts', EventImpactStatViewSet, basename='event-impact')
router.register(r'sentiment/news', NewsArticleViewSet, basename='sentiment-news')
router.register(r'sentiment', SentimentScoreViewSet, basename='sentiment-score')
router.register(r'sentiment/concepts', ConceptHeatViewSet, basename='sentiment-concept')
router.register(r'prediction', PredictionViewSet, basename='prediction')
router.register(r'prediction-model-versions', ModelVersionViewSet, basename='prediction-model-version')
router.register(r'lightgbm-predictions', LightGBMPredictionViewSet, basename='lightgbm-prediction')
router.register(r'lightgbm-models', LightGBMModelArtifactViewSet, basename='lightgbm-model')
router.register(r'ensemble-weights', EnsembleWeightSnapshotViewSet, basename='ensemble-weight')
router.register(r'backtest', BacktestRunViewSet, basename='backtest')
router.register(r'backtest-trades', BacktestTradeViewSet, basename='backtest-trade')

urlpatterns = [
    path('admin/', admin.site.urls),
    # API v1 endpoints
    path('api/v1/', include(router.urls)),
    # User registration and authentication
    path('api/v1/users/register/', UserRegistrationView.as_view(), name='user_register'),
    path('api/v1/users/verify-email/', EmailVerificationView.as_view(), name='email_verify'),
    path('api/v1/users/password-reset/', PasswordResetRequestView.as_view(), name='password_reset'),
    path('api/v1/users/password-reset-confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    # JWT authentication
    path('api/v1/auth/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/v1/auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/v1/auth/token/verify/', TokenVerifyView.as_view(), name='token_verify'),
    # Browsable API authentication
    path('api-auth/', include('rest_framework.urls', namespace='rest_framework')),
]
