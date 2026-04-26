from datetime import date, datetime
from decimal import Decimal
from time import sleep

from django.conf import settings


MACRO_FIELDS = [
    'dxy',
    'cny_usd',
    'cn10y_yield',
    'cn2y_yield',
    'pmi_manufacturing',
    'pmi_non_manufacturing',
    'cpi_yoy',
    'ppi_yoy',
]

DXY_TUSHARE_CODES = ['USDOLLAR.FXCM', 'USDOLLAR']
YIELD_CURVE_TYPE_PREFERENCE = ['0', '1']

CRITICAL_MACRO_FIELDS = [
    'pmi_manufacturing',
    'pmi_non_manufacturing',
    'cn10y_yield',
    'cn2y_yield',
    'cpi_yoy',
]


def _project_floor_date():
    floor_raw = getattr(settings, 'HISTORICAL_DATA_FLOOR', '2000-01-01')
    try:
        return date.fromisoformat(str(floor_raw))
    except ValueError:
        return date(2000, 1, 1)


def _parse_decimal(value):
    if value in (None, '', 'nan'):
        return None
    try:
        if value != value:
            return None
    except Exception:
        pass
    try:
        parsed = Decimal(str(value))
        if parsed.is_nan():
            return None
        return parsed
    except Exception:
        return None


def _yyyymm(dt):
    return dt.strftime('%Y%m')


def _month_start_from_yyyymm(value):
    token = str(value or '').strip()
    if len(token) < 6:
        return None
    try:
        return date(int(token[:4]), int(token[4:6]), 1)
    except ValueError:
        return None


def _month_start_from_any(value):
    if isinstance(value, date):
        return value.replace(day=1)

    token = str(value or '').strip()
    if not token:
        return None

    if len(token) == 6 and token.isdigit():
        return _month_start_from_yyyymm(token)

    for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%Y%m%d', '%Y-%m', '%Y/%m']:
        try:
            return datetime.strptime(token, fmt).date().replace(day=1)
        except ValueError:
            continue

    try:
        parsed = datetime.fromisoformat(token)
        return parsed.date().replace(day=1)
    except ValueError:
        return None


def _pick_value(row, keys):
    for key in keys:
        if key in row and row[key] not in (None, ''):
            return row[key]
    return None


def extract_pmi_month(row):
    if not isinstance(row, dict):
        return None

    month_value = _pick_value(row, ['month', 'MONTH'])
    month_dt = _month_start_from_any(month_value)
    if month_dt is not None:
        return month_dt

    create_time = _pick_value(row, ['CREATE_TIME', 'create_time'])
    return _month_start_from_any(create_time)


def normalize_cny_usd_from_usd_quote(value):
    quote = _parse_decimal(value)
    if quote in (None, Decimal('0')):
        return None
    try:
        return (Decimal('1') / quote).quantize(Decimal('0.0001'))
    except Exception:
        return None


def normalize_dxy_quote(value):
    quote = _parse_decimal(value)
    if quote is None:
        return None
    if quote >= Decimal('1000'):
        quote = quote / Decimal('100')
    try:
        return quote.quantize(Decimal('0.0001'))
    except Exception:
        return quote


def extract_fx_quote(row):
    bid_close = _parse_decimal(row.get('bid_close')) if isinstance(row, dict) else None
    ask_close = _parse_decimal(row.get('ask_close')) if isinstance(row, dict) else None
    if bid_close is not None and ask_close is not None:
        try:
            return ((bid_close + ask_close) / Decimal('2')).quantize(Decimal('0.0001'))
        except Exception:
            return (bid_close + ask_close) / Decimal('2')
    return _pick_value(row, ['close', 'bid_close', 'ask_close', 'price', 'last'])


def _normalize_curve_type(value):
    if value in (None, '', 'nan'):
        return ''
    try:
        if value != value:
            return ''
    except Exception:
        pass

    token = str(value).strip()
    if token.endswith('.0'):
        token = token[:-2]
    return token


def _curve_type_rank(value):
    token = _normalize_curve_type(value)
    if token in YIELD_CURVE_TYPE_PREFERENCE:
        return YIELD_CURVE_TYPE_PREFERENCE.index(token)
    return len(YIELD_CURVE_TYPE_PREFERENCE)


def select_preferred_yield_rows(yield_df):
    if yield_df is None or yield_df.empty:
        return yield_df

    working = yield_df.copy()
    working['trade_date_token'] = working['trade_date'].astype(str)
    if 'curve_type' in working.columns:
        working['curve_type_token'] = working['curve_type'].map(_normalize_curve_type)
    else:
        working['curve_type_token'] = ''
    working['curve_type_rank'] = working['curve_type_token'].map(_curve_type_rank)
    working = working.sort_values(['trade_date_token', 'curve_type_rank'], ascending=[False, True])
    return working.drop_duplicates(subset=['trade_date_token'], keep='first')


def build_monthly_yield_points(yield_df):
    preferred_rows = select_preferred_yield_rows(yield_df)
    if preferred_rows is None or preferred_rows.empty:
        return {}

    points = {}
    for row in preferred_rows.to_dict(orient='records'):
        trade_date = str(row.get('trade_date') or '').strip()
        month = trade_date[:6]
        if len(month) != 6 or month in points:
            continue

        yield_value = _parse_decimal(row.get('yield'))
        if yield_value is None:
            continue

        point = {
            'yield': yield_value,
            'trade_date': trade_date,
        }
        curve_type = _normalize_curve_type(row.get('curve_type'))
        if curve_type:
            point['curve_type'] = curve_type
        points[month] = point

    return points


def _empty_payload(snapshot_date, source):
    return {
        'date': snapshot_date,
        'dxy': None,
        'cny_usd': None,
        'cn10y_yield': None,
        'cn2y_yield': None,
        'pmi_manufacturing': None,
        'pmi_non_manufacturing': None,
        'cpi_yoy': None,
        'ppi_yoy': None,
        'metadata': {
            'source': source,
            'source_used': source,
        },
    }


def _payload_has_critical_values(payload):
    return any(
        payload.get(field) is not None
        for field in CRITICAL_MACRO_FIELDS
    )


def _safe_tushare_client():
    import tushare as ts

    token = getattr(settings, 'TUSHARE_TOKEN', None)
    if not token:
        return None
    return ts.pro_api(token)


def _sleep_if_needed():
    delay = float(getattr(settings, 'MACRO_SYNC_PROVIDER_SLEEP_SECONDS', 0) or 0)
    if delay > 0:
        sleep(delay)


def call_tushare_with_retries(fetcher, *, metadata=None, error_key=None, attempts=None, retry_sleep_seconds=None):
    max_attempts = attempts
    if max_attempts is None:
        max_attempts = getattr(settings, 'MACRO_SYNC_TUSHARE_MAX_RETRIES', 3)
    attempts = max(int(max_attempts or 3), 1)

    retry_sleep = retry_sleep_seconds
    if retry_sleep is None:
        retry_sleep = getattr(
            settings,
            'MACRO_SYNC_TUSHARE_RETRY_SLEEP_SECONDS',
            getattr(settings, 'MACRO_SYNC_PROVIDER_SLEEP_SECONDS', 0.2),
        )
    retry_sleep = float(retry_sleep or 0.2)
    last_exc = None

    for attempt in range(1, attempts + 1):
        try:
            result = fetcher()
            if metadata is not None and error_key and attempt > 1:
                metadata.setdefault('retries', {})[error_key] = attempt - 1
            return result
        except Exception as exc:
            last_exc = exc
            if metadata is not None and error_key:
                metadata.setdefault('retry_errors', {}).setdefault(error_key, []).append(str(exc))
            if attempt >= attempts:
                raise
            if retry_sleep > 0:
                sleep(retry_sleep * attempt)

    raise last_exc


def _annotate_field_sources(payload, source_key):
    if payload is None:
        return None

    metadata = payload.setdefault('metadata', {})
    field_sources = dict(metadata.get('field_sources') or {})
    for field in MACRO_FIELDS:
        if payload.get(field) is not None and field not in field_sources:
            field_sources[field] = source_key
    metadata['field_sources'] = field_sources
    metadata['source_used'] = metadata.get('source_used') or source_key
    return payload


def _missing_macro_fields(payload):
    if not payload:
        return list(MACRO_FIELDS)
    return [field for field in MACRO_FIELDS if payload.get(field) is None]


def _merge_missing_fields(primary_payload, fallback_payload, fallback_key):
    if not primary_payload:
        return _annotate_field_sources(fallback_payload, fallback_key)
    if not fallback_payload:
        return primary_payload

    metadata = primary_payload.setdefault('metadata', {})
    field_sources = dict(metadata.get('field_sources') or {})
    fallback_metadata = fallback_payload.get('metadata', {}) if fallback_payload else {}
    fallback_source = fallback_metadata.get('source_used') or fallback_key
    filled_fields = []

    for field in MACRO_FIELDS:
        if primary_payload.get(field) is None and fallback_payload.get(field) is not None:
            primary_payload[field] = fallback_payload[field]
            field_sources[field] = fallback_source
            filled_fields.append(field)

    metadata['field_sources'] = field_sources
    if filled_fields:
        metadata['fallback_source'] = fallback_key
        metadata['fallback_fields'] = filled_fields
        metadata['fallback_payload'] = fallback_metadata
        source_used = metadata.get('source_used') or metadata.get('source') or 'none'
        metadata['source_used'] = fallback_source if source_used == 'none' else f'{source_used}+{fallback_source}'

    return primary_payload


def fetch_macro_snapshot_from_tushare(snapshot_date=None):
    client = _safe_tushare_client()
    if client is None:
        return None

    as_of = snapshot_date or date.today().replace(day=1)
    payload = _empty_payload(as_of, 'tushare')

    try:
        cpi_df = call_tushare_with_retries(
            lambda: client.cn_cpi(limit=1),
            metadata=payload['metadata'],
            error_key='cpi',
        )
        _sleep_if_needed()
        if cpi_df is not None and not cpi_df.empty:
            row = cpi_df.iloc[0].to_dict()
            month_dt = _month_start_from_any(row.get('month'))
            if month_dt:
                payload['date'] = month_dt
            payload['cpi_yoy'] = _parse_decimal(_pick_value(row, ['nt_yoy', 'town_yoy', 'cnt_yoy']))
    except Exception as exc:
        payload['metadata']['cpi_error'] = str(exc)

    try:
        ppi_df = call_tushare_with_retries(
            lambda: client.cn_ppi(limit=1),
            metadata=payload['metadata'],
            error_key='ppi',
        )
        _sleep_if_needed()
        if ppi_df is not None and not ppi_df.empty:
            row = ppi_df.iloc[0].to_dict()
            month_dt = _month_start_from_any(row.get('month'))
            if month_dt:
                payload['date'] = month_dt
            payload['ppi_yoy'] = _parse_decimal(_pick_value(row, ['ppi_yoy']))
    except Exception as exc:
        payload['metadata']['ppi_error'] = str(exc)

    try:
        pmi_df = call_tushare_with_retries(
            lambda: client.cn_pmi(limit=1),
            metadata=payload['metadata'],
            error_key='pmi',
        )
        _sleep_if_needed()
        if pmi_df is not None and not pmi_df.empty:
            row = pmi_df.iloc[0].to_dict()
            month_dt = extract_pmi_month(row)
            if month_dt:
                payload['date'] = month_dt
            payload['pmi_manufacturing'] = _parse_decimal(_pick_value(row, ['PMI010000']))
            payload['pmi_non_manufacturing'] = _parse_decimal(_pick_value(row, ['PMI020100']))
    except Exception as exc:
        payload['metadata']['pmi_error'] = str(exc)

    start_date = as_of.replace(day=1).strftime('%Y%m01')
    end_date = as_of.strftime('%Y%m%d')
    for term, field in [(10, 'cn10y_yield'), (2, 'cn2y_yield')]:
        try:
            y_df = call_tushare_with_retries(
                lambda term=term: client.yc_cb(curve_term=term, start_date=start_date, end_date=end_date, limit=50),
                metadata=payload['metadata'],
                error_key=f'yield_{term}y',
            )
            _sleep_if_needed()
            if y_df is not None and not y_df.empty:
                preferred_rows = select_preferred_yield_rows(y_df)
                if preferred_rows is not None and not preferred_rows.empty:
                    row = preferred_rows.iloc[0].to_dict()
                    payload[field] = _parse_decimal(row.get('yield'))
                    payload['metadata'].setdefault('yield_sources', {})[field] = {
                        'source': 'tushare_yc_cb',
                        'curve_term': term,
                        'trade_date': str(row.get('trade_date') or ''),
                    }
                    curve_type = _normalize_curve_type(row.get('curve_type'))
                    if curve_type:
                        payload['metadata']['yield_sources'][field]['curve_type'] = curve_type
        except Exception as exc:
            payload['metadata'][f'yield_{term}y_error'] = str(exc)

    fx_specs = [
        ('dxy', DXY_TUSHARE_CODES, normalize_dxy_quote),
        ('cny_usd', ['USDCNH.FXCM'], normalize_cny_usd_from_usd_quote),
    ]
    for field, ts_codes, normalizer in fx_specs:
        field_errors = []
        for ts_code in ts_codes:
            try:
                fx_df = call_tushare_with_retries(
                    lambda ts_code=ts_code: client.fx_daily(
                        ts_code=ts_code,
                        start_date=start_date,
                        end_date=end_date,
                        limit=50,
                    ),
                    metadata=payload['metadata'],
                    error_key=f'{field}_{ts_code}',
                )
                _sleep_if_needed()
                if fx_df is None or fx_df.empty:
                    continue

                sorted_df = fx_df.sort_values('trade_date', ascending=False)
                close_value = extract_fx_quote(sorted_df.iloc[0].to_dict())
                normalized_value = normalizer(close_value)
                if normalized_value is None:
                    continue

                payload[field] = normalized_value
                payload['metadata'].setdefault('fx_quote_sources', {})[field] = ts_code
            except Exception as exc:
                field_errors.append(f'{ts_code}: {exc}')

        if payload.get(field) is None and field_errors:
            payload['metadata'][f'{field}_error'] = '; '.join(field_errors)

    return _annotate_field_sources(payload, 'tushare')


def _safe_call_akshare(function_name, **kwargs):
    try:
        import akshare as ak
    except Exception:
        return None

    fn = getattr(ak, function_name, None)
    if fn is None:
        return None

    try:
        return fn(**kwargs)
    except TypeError:
        try:
            return fn()
        except Exception:
            return None
    except Exception:
        return None


def fetch_macro_snapshot_from_akshare(snapshot_date=None):
    as_of = snapshot_date or date.today().replace(day=1)
    payload = _empty_payload(as_of, 'akshare')

    cpi_df = _safe_call_akshare('macro_china_cpi_yearly')
    if cpi_df is not None and not cpi_df.empty:
        row = cpi_df.iloc[-1].to_dict()
        payload['cpi_yoy'] = _parse_decimal(_pick_value(row, ['value', '今值', '同比增长']))

    ppi_df = _safe_call_akshare('macro_china_ppi_yearly')
    if ppi_df is not None and not ppi_df.empty:
        row = ppi_df.iloc[-1].to_dict()
        payload['ppi_yoy'] = _parse_decimal(_pick_value(row, ['value', '今值', '同比增长']))

    pmi_df = _safe_call_akshare('macro_china_pmi_yearly')
    if pmi_df is not None and not pmi_df.empty:
        row = pmi_df.iloc[-1].to_dict()
        payload['pmi_manufacturing'] = _parse_decimal(_pick_value(row, ['value', '今值', '制造业PMI']))

    fx_df = _safe_call_akshare('fx_spot_quote')
    if fx_df is not None and not fx_df.empty:
        cny_candidates = fx_df[fx_df.astype(str).apply(lambda col: col.str.contains('USD/CNY|USDCNY', case=False, regex=True)).any(axis=1)]
        if not cny_candidates.empty:
            row = cny_candidates.iloc[0].to_dict()
            payload['cny_usd'] = normalize_cny_usd_from_usd_quote(_pick_value(row, ['最新价', 'price', 'last']))
            payload['metadata'].setdefault('fx_quote_sources', {})['cny_usd'] = 'fx_spot_quote:USD/CNY'

    payload['metadata']['fallback_note'] = 'AkShare coverage may be partial for some fields.'
    return _annotate_field_sources(payload, 'akshare')


def fetch_macro_snapshot_with_fallback(snapshot_date=None, primary='tushare', fallback='akshare'):
    primary_key = str(primary or 'tushare').strip().lower()
    fallback_key = str(fallback or 'akshare').strip().lower()

    source_fetchers = {
        'tushare': fetch_macro_snapshot_from_tushare,
        'akshare': fetch_macro_snapshot_from_akshare,
    }

    primary_payload = None
    primary_error = None
    fetch_primary = source_fetchers.get(primary_key)
    if fetch_primary:
        try:
            primary_payload = fetch_primary(snapshot_date=snapshot_date)
        except Exception as exc:
            primary_error = str(exc)

    if primary_payload:
        primary_payload = _annotate_field_sources(primary_payload, primary_key)
        if primary_error:
            primary_payload['metadata']['primary_error'] = primary_error
        missing_fields = _missing_macro_fields(primary_payload)
        if not missing_fields:
            primary_payload['metadata']['source_used'] = primary_key
            return primary_payload
    else:
        missing_fields = list(MACRO_FIELDS)

    fallback_payload = None
    fallback_error = None
    fetch_fallback = source_fetchers.get(fallback_key)
    if fetch_fallback and missing_fields:
        try:
            fallback_payload = fetch_fallback(snapshot_date=snapshot_date)
        except Exception as exc:
            fallback_error = str(exc)

    if fallback_payload:
        fallback_payload = _annotate_field_sources(fallback_payload, fallback_key)
        if primary_payload:
            merged_payload = _merge_missing_fields(primary_payload, fallback_payload, fallback_key)
            merged_payload['metadata']['primary_source'] = primary_key
            if fallback_error:
                merged_payload['metadata']['fallback_error'] = fallback_error
            return merged_payload
        fallback_payload['metadata']['source_used'] = fallback_key
        fallback_payload['metadata']['primary_source'] = primary_key
        if primary_error:
            fallback_payload['metadata']['primary_error'] = primary_error
        if fallback_error:
            fallback_payload['metadata']['fallback_error'] = fallback_error
        return fallback_payload

    if primary_payload:
        primary_payload['metadata']['source_used'] = primary_key
        primary_payload['metadata']['primary_source'] = primary_key
        primary_payload['metadata']['fallback_source'] = fallback_key
        if fallback_error:
            primary_payload['metadata']['fallback_error'] = fallback_error
        return primary_payload

    floor_dt = _project_floor_date()
    resolved = snapshot_date or date.today().replace(day=1)
    if resolved < floor_dt:
        resolved = floor_dt

    payload = _empty_payload(resolved, 'none')
    payload['metadata']['source_used'] = 'none'
    payload['metadata']['primary_source'] = primary_key
    payload['metadata']['fallback_source'] = fallback_key
    if primary_error:
        payload['metadata']['primary_error'] = primary_error
    if fallback_error:
        payload['metadata']['fallback_error'] = fallback_error
    return payload


def fetch_earliest_macro_availability(primary='tushare'):
    primary_key = str(primary or 'tushare').strip().lower()
    result = {
        'source': primary_key,
        'project_floor': str(_project_floor_date()),
    }

    if primary_key != 'tushare':
        result['error'] = 'Earliest probe currently supports tushare only.'
        return result

    client = _safe_tushare_client()
    if client is None:
        result['error'] = 'TUSHARE_TOKEN is not configured.'
        return result

    try:
        cpi_df = client.cn_cpi(limit=5000)
        if cpi_df is not None and not cpi_df.empty:
            result['cpi_min_month'] = str(cpi_df['month'].astype(str).min())
            result['cpi_max_month'] = str(cpi_df['month'].astype(str).max())
    except Exception as exc:
        result['cpi_error'] = str(exc)

    try:
        ppi_df = client.cn_ppi(limit=5000)
        if ppi_df is not None and not ppi_df.empty:
            result['ppi_min_month'] = str(ppi_df['month'].astype(str).min())
            result['ppi_max_month'] = str(ppi_df['month'].astype(str).max())
    except Exception as exc:
        result['ppi_error'] = str(exc)

    try:
        pmi_df = client.cn_pmi(limit=5000)
        if pmi_df is not None and not pmi_df.empty:
            if 'month' in pmi_df.columns:
                result['pmi_min_month'] = str(pmi_df['month'].astype(str).min())
                result['pmi_max_month'] = str(pmi_df['month'].astype(str).max())
            elif 'CREATE_TIME' in pmi_df.columns:
                parsed = pmi_df['CREATE_TIME'].astype(str)
                result['pmi_min_create_time'] = str(parsed.min())
                result['pmi_max_create_time'] = str(parsed.max())
    except Exception as exc:
        result['pmi_error'] = str(exc)

    return result
