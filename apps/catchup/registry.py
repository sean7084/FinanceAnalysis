"""The series this system knows how to catch up, in dependency order.

``priority`` is the execution order and encodes the dependency chain from
runbook-stale-data.md §4.1: OHLCV + calendar + suspensions + listing dates, then
indicators/signals, then RS_SCORE/sentiment/factors, then predictions. Repairing a
downstream stage before its inputs reproduces the same gap, so the ordering is not
a preference.

``beat_task_name`` feeds two things: the watermark detectors for class-B series,
and ``CatchUpRun.cause`` for everything else. Three of these deliberately point at
the daily dispatcher rather than at an entry named after the series:

* ``index_membership`` -- the monthly ``sync-index-memberships-monthly`` entry can
  legitimately be 30 days old, so using it as a watermark would report an outage
  every single day. Membership is actually refreshed daily over a trailing 30-day
  window by ``sync_daily_a_shares``, whose watermark is the honest one.
* ``technical_indicators`` / ``factor_scores`` -- these have no beat entry at all.
  They run inside ``run_post_sync_universal_refresh``, which is a chord callback
  off the daily dispatcher, so the dispatcher's watermark is the only evidence.
* ``predictions`` -- not a watermark at all. Its beat entry fires hourly and
  self-heals, so ``last_run_at`` is never stale and would hide the very holes this
  exists to find. It uses the data-level ``coverage_gaps`` instead.
"""

from dataclasses import dataclass, replace
from typing import Callable

from . import detectors, repairs
from .detectors import DAILY_DISPATCHER_ENTRY, KIND_REPLAY

LAYER_RAW = 'raw'
LAYER_DERIVED = 'derived'

# Cost shapes. 'per_date' scales with the hole's length; 'per_asset' does not,
# because those commands fetch multi-year windows per asset and so cost the same
# for a one-day hole as for a three-week one; 'local' spends no provider quota.
COST_PER_DATE = 'per_date'
COST_PER_ASSET = 'per_asset'
COST_FIXED = 'fixed'
COST_LOCAL = 'local'


@dataclass(frozen=True)
class SeriesDescriptor:
    name: str
    priority: int
    layer: str
    depends_on: tuple
    detector: Callable
    repair: Callable
    cost_shape: str
    calls_per_unit: int
    beat_task_name: str | None = None
    detector_class: str = 'A'


def _macro_yields_detector(ctx):
    """Same month-start question as macro_core, reported under its own series name.

    The repair is what differs: it short-circuits on the cached entitlement denial
    instead of sleeping 31s per tenor to rediscover it.
    """
    return replace(detectors.detect_macro_gap(ctx), series='macro_yields')


SERIES = (
    SeriesDescriptor(
        name='trading_calendar', priority=10, layer=LAYER_RAW, depends_on=(),
        detector=detectors.detect_calendar_gap, repair=repairs.repair_calendar,
        cost_shape=COST_FIXED, calls_per_unit=2,
        beat_task_name=DAILY_DISPATCHER_ENTRY, detector_class='A',
    ),
    SeriesDescriptor(
        name='suspensions', priority=20, layer=LAYER_RAW,
        depends_on=('trading_calendar',),
        detector=detectors.watermark_detector(
            'suspensions', DAILY_DISPATCHER_ENTRY, grace_days=1, estimated_calls=1),
        repair=repairs.repair_suspensions,
        cost_shape=COST_FIXED, calls_per_unit=1,
        beat_task_name=DAILY_DISPATCHER_ENTRY, detector_class='B',
    ),
    SeriesDescriptor(
        name='ohlcv', priority=30, layer=LAYER_RAW,
        depends_on=('trading_calendar', 'suspensions'),
        detector=detectors.detect_ohlcv_gap, repair=repairs.repair_ohlcv,
        cost_shape=COST_FIXED, calls_per_unit=0,
        beat_task_name=DAILY_DISPATCHER_ENTRY, detector_class='C',
    ),
    SeriesDescriptor(
        name='benchmark_index', priority=35, layer=LAYER_RAW,
        depends_on=('trading_calendar',),
        detector=detectors.detect_benchmark_gap, repair=repairs.repair_benchmark,
        cost_shape=COST_FIXED, calls_per_unit=2,
        beat_task_name='sync-etf-benchmark-index-daily', detector_class='A',
    ),
    SeriesDescriptor(
        name='index_membership', priority=40, layer=LAYER_RAW,
        depends_on=('trading_calendar',),
        detector=detectors.watermark_detector(
            'index_membership', DAILY_DISPATCHER_ENTRY, grace_days=1, estimated_calls=4),
        repair=repairs.repair_membership,
        cost_shape=COST_FIXED, calls_per_unit=4,
        beat_task_name=DAILY_DISPATCHER_ENTRY, detector_class='B',
    ),
    SeriesDescriptor(
        name='etf_daily', priority=45, layer=LAYER_RAW,
        depends_on=('trading_calendar',),
        detector=detectors.detect_etf_daily_gap, repair=repairs.repair_etf_daily,
        cost_shape=COST_PER_DATE, calls_per_unit=2,
        beat_task_name='sync-etf-daily-bars-daily', detector_class='A',
    ),
    SeriesDescriptor(
        name='capital_flow', priority=50, layer=LAYER_RAW,
        depends_on=('trading_calendar', 'ohlcv'),
        detector=detectors.detect_capital_flow_gap, repair=repairs.repair_capital_flow,
        cost_shape=COST_PER_ASSET, calls_per_unit=2, detector_class='C',
    ),
    SeriesDescriptor(
        name='fundamentals', priority=55, layer=LAYER_RAW,
        depends_on=('ohlcv',),
        detector=detectors.detect_fundamentals_gap, repair=repairs.repair_fundamentals,
        cost_shape=COST_PER_ASSET, calls_per_unit=3, detector_class='C',
    ),
    SeriesDescriptor(
        name='news', priority=60, layer=LAYER_RAW, depends_on=(),
        detector=detectors.watermark_detector(
            'news', 'fetch-latest-market-news-daily', grace_days=1, estimated_calls=3),
        repair=repairs.repair_news,
        cost_shape=COST_FIXED, calls_per_unit=3,
        beat_task_name='fetch-latest-market-news-daily', detector_class='B',
    ),
    SeriesDescriptor(
        name='macro_core', priority=70, layer=LAYER_RAW, depends_on=(),
        detector=detectors.detect_macro_gap, repair=repairs.repair_macro_core,
        cost_shape=COST_FIXED, calls_per_unit=5,
        beat_task_name='sync-macro-data-monthly', detector_class='A',
    ),
    SeriesDescriptor(
        name='macro_yields', priority=71, layer=LAYER_RAW, depends_on=(),
        detector=_macro_yields_detector, repair=repairs.repair_macro_yields,
        cost_shape=COST_FIXED, calls_per_unit=12,
        beat_task_name='sync-macro-data-monthly', detector_class='B',
    ),
    SeriesDescriptor(
        name='technical_indicators', priority=80, layer=LAYER_DERIVED,
        depends_on=('ohlcv', 'suspensions'),
        detector=detectors.watermark_detector(
            'technical_indicators', DAILY_DISPATCHER_ENTRY, grace_days=1, kind=KIND_REPLAY),
        repair=repairs.replay_technical_indicators,
        cost_shape=COST_LOCAL, calls_per_unit=0,
        beat_task_name=DAILY_DISPATCHER_ENTRY, detector_class='B',
    ),
    SeriesDescriptor(
        name='model_data_rs_score', priority=85, layer=LAYER_DERIVED,
        depends_on=('technical_indicators', 'benchmark_index'),
        detector=detectors.watermark_detector(
            'model_data_rs_score', DAILY_DISPATCHER_ENTRY, grace_days=1, kind=KIND_REPLAY),
        repair=repairs.replay_model_data,
        cost_shape=COST_LOCAL, calls_per_unit=0,
        beat_task_name=DAILY_DISPATCHER_ENTRY, detector_class='B',
    ),
    SeriesDescriptor(
        name='factor_scores', priority=87, layer=LAYER_DERIVED,
        depends_on=('model_data_rs_score', 'capital_flow'),
        detector=detectors.watermark_detector(
            'factor_scores', DAILY_DISPATCHER_ENTRY, grace_days=1, kind=KIND_REPLAY),
        repair=repairs.replay_factor_scores,
        cost_shape=COST_LOCAL, calls_per_unit=0,
        beat_task_name=DAILY_DISPATCHER_ENTRY, detector_class='B',
    ),
    SeriesDescriptor(
        name='sentiment_pipeline', priority=90, layer=LAYER_DERIVED,
        depends_on=('news',),
        detector=detectors.watermark_detector(
            'sentiment_pipeline', 'run-daily-sentiment-pipeline',
            grace_days=1, kind=KIND_REPLAY),
        repair=repairs.replay_sentiment_pipeline,
        cost_shape=COST_LOCAL, calls_per_unit=0,
        beat_task_name='run-daily-sentiment-pipeline', detector_class='B',
    ),
    SeriesDescriptor(
        name='etf_features', priority=92, layer=LAYER_DERIVED,
        depends_on=('etf_daily', 'benchmark_index'),
        detector=detectors.watermark_detector(
            'etf_features', 'compute-etf-features-daily', grace_days=1, kind=KIND_REPLAY),
        repair=repairs.replay_etf_features,
        cost_shape=COST_LOCAL, calls_per_unit=0,
        beat_task_name='compute-etf-features-daily', detector_class='B',
    ),
    SeriesDescriptor(
        name='predictions', priority=95, layer=LAYER_DERIVED,
        depends_on=('factor_scores', 'sentiment_pipeline'),
        detector=detectors.detect_prediction_gap, repair=repairs.delegate_predictions,
        cost_shape=COST_LOCAL, calls_per_unit=0,
        beat_task_name='reconcile-prediction-coverage-hourly', detector_class='A',
    ),
)

BY_NAME = {descriptor.name: descriptor for descriptor in SERIES}
CALENDAR_SERIES = 'trading_calendar'


def ordered(names=None):
    """Descriptors in priority order, optionally filtered to ``names``."""
    selected = SERIES if not names else tuple(
        descriptor for descriptor in SERIES if descriptor.name in set(names)
    )
    return tuple(sorted(selected, key=lambda d: d.priority))


def all_names():
    return tuple(descriptor.name for descriptor in SERIES)
