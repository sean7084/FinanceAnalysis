from datetime import date, datetime
from decimal import Decimal
from time import sleep

from django.conf import settings


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
        return Decimal(str(value))
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
        for field in ['pmi_manufacturing', 'pmi_non_manufacturing', 'cn10y_yield', 'cn2y_yield', 'cpi_yoy']
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


def fetch_macro_snapshot_from_tushare(snapshot_date=None):
    client = _safe_tushare_client()
    if client is None:
        return None

    as_of = snapshot_date or date.today().replace(day=1)
    payload = _empty_payload(as_of, 'tushare')

    try:
        cpi_df = client.cn_cpi(limit=1)
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
        ppi_df = client.cn_ppi(limit=1)
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
        pmi_df = client.cn_pmi(limit=1)
        _sleep_if_needed()
        if pmi_df is not None and not pmi_df.empty:
            row = pmi_df.iloc[0].to_dict()
            month_dt = _month_start_from_any(row.get('month') or row.get('CREATE_TIME'))
            if month_dt:
                payload['date'] = month_dt
            payload['pmi_manufacturing'] = _parse_decimal(_pick_value(row, ['PMI010000']))
            payload['pmi_non_manufacturing'] = _parse_decimal(_pick_value(row, ['PMI030000']))
    except Exception as exc:
        payload['metadata']['pmi_error'] = str(exc)

    start_date = as_of.replace(day=1).strftime('%Y%m01')
    end_date = as_of.strftime('%Y%m%d')
    for term, field in [(10, 'cn10y_yield'), (2, 'cn2y_yield')]:
        try:
            y_df = client.yc_cb(curve_term=term, start_date=start_date, end_date=end_date, limit=50)
            _sleep_if_needed()
            if y_df is not None and not y_df.empty:
                sorted_df = y_df.sort_values('trade_date', ascending=False)
                payload[field] = _parse_decimal(sorted_df.iloc[0].to_dict().get('yield'))
        except Exception as exc:
            payload['metadata'][f'yield_{term}y_error'] = str(exc)

    return payload


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
            payload['cny_usd'] = _parse_decimal(_pick_value(row, ['最新价', 'price', 'last']))

    payload['metadata']['fallback_note'] = 'AkShare coverage may be partial for some fields.'
    return payload


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

    if primary_payload and _payload_has_critical_values(primary_payload):
        if primary_error:
            primary_payload['metadata']['primary_error'] = primary_error
        primary_payload['metadata']['source_used'] = primary_key
        return primary_payload

    fallback_payload = None
    fallback_error = None
    fetch_fallback = source_fetchers.get(fallback_key)
    if fetch_fallback:
        try:
            fallback_payload = fetch_fallback(snapshot_date=snapshot_date)
        except Exception as exc:
            fallback_error = str(exc)

    if fallback_payload:
        fallback_payload['metadata']['source_used'] = fallback_key
        fallback_payload['metadata']['primary_source'] = primary_key
        if primary_error:
            fallback_payload['metadata']['primary_error'] = primary_error
        if fallback_error:
            fallback_payload['metadata']['fallback_error'] = fallback_error
        return fallback_payload

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
