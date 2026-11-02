"""Shift legacy ``NewsArticle.published_at`` from fake-UTC to real Beijing time.

``apps/sentiment/providers.py`` parses the wall-clock strings that the Chinese
news providers publish (Eastmoney/Sina/Tonghuashun publish Beijing time, as does
TuShare) into a naive datetime and then localises it with ``make_aware()``,
which uses ``settings.TIME_ZONE``. While that setting was ``'UTC'`` an article
published at 09:30 Beijing was stored as ``09:30+00:00`` -- an instant eight
hours ahead of the truth.

The timezone unification flips ``TIME_ZONE`` to ``'Asia/Shanghai'``, which
makes ``make_aware()`` do the right thing for every row ingested from now on.
This migration corrects the rows stored before that flip, so the two eras agree.

Why this does not double-correct anything
-----------------------------------------
The affected row set is identified by a predicate, not a timestamp cutoff. A
legacy mis-stamped row satisfies exactly one property: ``published_at`` equals
the provider's own wall-clock string, read as UTC. The raw string survives in
``metadata['raw']``. A row written after the flip stores the same string read
as Asia/Shanghai, whose offset is non-zero, so it can never equal the UTC
reading and excludes itself. A row that fell through to the ``timezone.now()``
fallback carries a true instant and no matching raw string, and also excludes
itself. A cutoff on ``created_at`` would be strictly worse: it would "fix" the
fallback rows and miss rows an old-code worker inserts after ``migrate`` runs.

Why no ``SentimentScore`` recompute is needed
--------------------------------------------
The old daily bucket was the UTC date of the mis-stamped value, which is the
article's Beijing *wall* date ``W``. The corrected value is ``W`` interpreted as
Asia/Shanghai, whose Beijing date is still ``W``. The day bucket is invariant
under this shift, so ``SentimentScore`` (all three ``score_type`` values, each
keyed on ``date``) and ``ConceptHeat`` keep pointing at the right day.

Residual: articles ingested through the ``timezone.now()`` fallback were
bucketed by UTC date, which can differ from their Beijing date. They cannot be
identified after the fact, and the daily sentiment score they feed is a QA
surface rather than a model feature.

Reversible and idempotent: forward marks each row it touches in ``metadata``,
reverse keys off that mark, and a shifted row no longer matches the forward
predicate so it can never move twice.
"""

from datetime import datetime
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

MARKER_KEY = 'tz_shift'
MARKER_VALUE = 'legacy-utc-minus-8h-v1'
BEIJING = ZoneInfo('Asia/Shanghai')

# Time key per provider inside ``metadata['raw']``. A dict, tried in order,
# because a row's source label and the payload shape it arrived with are not
# guaranteed to agree for anything ingested before the source column existed.
RAW_TIME_KEYS = ('published_at', 'time', 'pub_time', 'date')

# Copied from ``providers._coerce_datetime`` on purpose: a migration must not
# import application code that later changes underneath it.
FORMATS = ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d', '%Y%m%d%H%M%S', '%Y%m%d')

BATCH_UPDATE = 1000


def _parse_raw_wall_clock(metadata):
    """The provider's own timestamp for this row, as a naive datetime, or None."""
    raw = (metadata or {}).get('raw')
    if not isinstance(raw, dict):
        return None
    for key in RAW_TIME_KEYS:
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        cleaned = value.strip()
        for fmt in FORMATS:
            try:
                return datetime.strptime(cleaned, fmt)
            except ValueError:
                continue
    return None


def _shift_legacy_rows(NewsArticle):
    counted = {'shifted': 0, 'already_marked': 0, 'no_raw_payload': 0,
               'mismatch_untouched': 0, 'total_scanned': 0}
    by_source = {}
    batch = []

    queryset = NewsArticle.objects.only('id', 'source', 'published_at', 'metadata')
    for article in queryset.iterator(chunk_size=2000):
        counted['total_scanned'] += 1
        metadata = article.metadata or {}
        if metadata.get(MARKER_KEY) == MARKER_VALUE:
            counted['already_marked'] += 1
            continue

        naive = _parse_raw_wall_clock(metadata)
        if naive is None:
            counted['no_raw_payload'] += 1
            continue

        # The signature of the bug: the stored instant is the provider's Beijing
        # wall clock read as though it were UTC.
        if article.published_at != naive.replace(tzinfo=dt_timezone.utc):
            counted['mismatch_untouched'] += 1
            continue

        article.published_at = naive.replace(tzinfo=BEIJING)
        article.metadata = {**metadata, MARKER_KEY: MARKER_VALUE}
        batch.append(article)
        counted['shifted'] += 1
        by_source[article.source] = by_source.get(article.source, 0) + 1

        if len(batch) >= BATCH_UPDATE:
            NewsArticle.objects.bulk_update(batch, ['published_at', 'metadata'])
            batch = []

    if batch:
        NewsArticle.objects.bulk_update(batch, ['published_at', 'metadata'])

    print('sentiment 0003 forward: '
          'shifted={shifted} already_marked={already_marked} '
          'no_raw_payload={no_raw_payload} mismatch_untouched={mismatch_untouched} '
          'total_scanned={total_scanned}'.format(**counted))
    print('sentiment 0003 forward shifted by source: '
          + (', '.join(f'{k}={v}' for k, v in sorted(by_source.items())) or 'none'))
    return counted


def _restore_marked_rows(NewsArticle):
    reverted = 0
    batch = []

    queryset = NewsArticle.objects.only('id', 'published_at', 'metadata')
    for article in queryset.iterator(chunk_size=2000):
        metadata = article.metadata or {}
        if metadata.get(MARKER_KEY) != MARKER_VALUE:
            continue

        # Exact inverse: the wall clock of the corrected instant, read as UTC.
        # Asia/Shanghai has no DST, so wall time and instant are bijective.
        wall = article.published_at.astimezone(BEIJING).replace(tzinfo=None)
        article.published_at = wall.replace(tzinfo=dt_timezone.utc)
        article.metadata = {k: v for k, v in metadata.items() if k != MARKER_KEY}
        batch.append(article)
        reverted += 1

        if len(batch) >= BATCH_UPDATE:
            NewsArticle.objects.bulk_update(batch, ['published_at', 'metadata'])
            batch = []

    if batch:
        NewsArticle.objects.bulk_update(batch, ['published_at', 'metadata'])

    print(f'sentiment 0003 reverse: reverted={reverted}')
    return reverted


def forward(apps, schema_editor):
    _shift_legacy_rows(apps.get_model('sentiment', 'NewsArticle'))


def reverse(apps, schema_editor):
    _restore_marked_rows(apps.get_model('sentiment', 'NewsArticle'))
