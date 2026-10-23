"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
"""
from django.contrib import admin
from django.urls import path, include, re_path
from rest_framework.routers import DefaultRouter
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)

from apps.core.views import spa_fallback
from apps.markets.views import MarketViewSet, AssetViewSet, OHLCVViewSet
from apps.analytics.views import (
    TechnicalIndicatorViewSet,
    DashboardStockViewSet,
    ScreenerTemplateViewSet,
    ScreenerViewSet,
    AlertRuleViewSet,
    AlertEventViewSet,
    SignalEventViewSet,
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
from apps.prediction.views_lstm import (
    LSTMPredictionViewSet,
)
from apps.backtest.views import (
    BacktestRunViewSet,
    BacktestTradeViewSet,
)
from apps.etf_rotation.views import (
    ETFUniverseViewSet,
    ETFDailyViewSet,
    ETFFeatureSnapshotViewSet,
    ETFRotationSignalViewSet,
    ETFRotationPositionViewSet,
    ETFRotationBacktestViewSet,
)
from apps.developer.views import (
    ChangelogEntryViewSet,
)

# Create a router and register our viewsets
router = DefaultRouter()
router.register(r'markets', MarketViewSet, basename='market')
router.register(r'assets', AssetViewSet, basename='asset')
router.register(r'ohlcv', OHLCVViewSet, basename='ohlcv')
router.register(r'indicators', TechnicalIndicatorViewSet, basename='indicator')
router.register(r'dashboard/stocks', DashboardStockViewSet, basename='dashboard-stock')
router.register(r'screener-templates', ScreenerTemplateViewSet, basename='screener-template')
router.register(r'screeners', ScreenerViewSet, basename='screener')
router.register(r'alerts', AlertRuleViewSet, basename='alert-rule')
router.register(r'alert-events', AlertEventViewSet, basename='alert-event')
router.register(r'signals', SignalEventViewSet, basename='signal-event')
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
router.register(r'lstm-predictions', LSTMPredictionViewSet, basename='lstm-prediction')
router.register(r'lightgbm-models', LightGBMModelArtifactViewSet, basename='lightgbm-model')
router.register(r'ensemble-weights', EnsembleWeightSnapshotViewSet, basename='ensemble-weight')
router.register(r'backtest', BacktestRunViewSet, basename='backtest')
router.register(r'backtest-trades', BacktestTradeViewSet, basename='backtest-trade')
# The ETF rotation endpoints are namespaced under ``etf/`` because ``signals`` and
# ``positions`` are already taken by the stock-side SignalEventViewSet and by the
# prediction surface; a second registration on those prefixes would shadow them.
router.register(r'etf/universe', ETFUniverseViewSet, basename='etf-universe')
router.register(r'etf/daily', ETFDailyViewSet, basename='etf-daily')
router.register(r'etf/features', ETFFeatureSnapshotViewSet, basename='etf-feature')
router.register(r'etf/signals', ETFRotationSignalViewSet, basename='etf-signal')
router.register(r'etf/positions', ETFRotationPositionViewSet, basename='etf-position')
router.register(r'etf/backtests', ETFRotationBacktestViewSet, basename='etf-backtest')
router.register(r'developer/changelog', ChangelogEntryViewSet, basename='developer-changelog')

urlpatterns = [
    # Bare origin serves the SPA shell. Registered first so `/` never falls
    # through to the catch-all below (which excludes empty paths by design).
    path('', spa_fallback, name='spa-root'),
    path('admin/', admin.site.urls),
    # API v1 endpoints
    path('api/v1/', include(router.urls)),
    # The defensive-asset picker lives at ``etf/defensive_assets/`` rather than the
    # ``etf/universe/defensive_assets/`` a router ``@action`` would produce, because it is a
    # shortlist of alternatives to a request field and not a slice of the universe listing.
    # Registered after the include so the router keeps precedence for anything it can match.
    path(
        'api/v1/etf/defensive_assets/',
        ETFUniverseViewSet.as_view({'get': 'defensive_assets'}),
        name='etf-defensive-assets',
    ),
    # OpenAPI schema endpoints (drf-spectacular)
    path('api/v1/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/v1/schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/v1/schema/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    # SPA fallback -- must stay LAST. The negative lookahead keeps DRF, admin,
    # static, media, and Channels WebSocket paths out of the catch-all so those
    # subsystems keep returning their own 404s and redirects instead of the SPA
    # shell.
    re_path(r'^(?!api/|admin/|static/|media/|ws/).*$', spa_fallback, name='spa-fallback'),
]
