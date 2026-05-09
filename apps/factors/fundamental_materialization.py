from datetime import timedelta
from decimal import Decimal

import pandas as pd


FUNDAMENTAL_DAILY_BASIC_COLUMNS = (
    'pe',
    'pb',
    'total_share',
    'float_share',
    'free_share',
    'total_mv',
    'circ_mv',
)
FUNDAMENTAL_SNAPSHOT_COLUMNS = FUNDAMENTAL_DAILY_BASIC_COLUMNS + ('roe', 'roe_qoq')


def safe_decimal(value):
    if value in (None, '', 'nan'):
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        return Decimal(str(value))
    except Exception:
        return None


def normalize_rate(value):
    parsed = safe_decimal(value)
    if parsed is None:
        return None
    if abs(parsed) > Decimal('1'):
        return parsed / Decimal('100')
    return parsed


def iter_date_windows(start_date, end_date, window_days=365 * 5):
    cursor = start_date
    while cursor <= end_date:
        window_end = min(end_date, cursor + timedelta(days=window_days - 1))
        yield cursor, window_end
        cursor = window_end + timedelta(days=1)


def empty_daily_basic_frame():
    return pd.DataFrame(columns=['date', 'daily_basic_trade_date', *FUNDAMENTAL_DAILY_BASIC_COLUMNS])


def empty_fina_indicator_frame():
    return pd.DataFrame(columns=['available_date', 'ann_date', 'report_end_date', 'roe', 'roe_qoq'])


def normalize_daily_basic_frame(daily_df):
    if daily_df is None or daily_df.empty:
        return empty_daily_basic_frame()

    normalized = daily_df.copy()
    if 'date' not in normalized.columns:
        normalized['date'] = pd.to_datetime(normalized.get('trade_date'), format='%Y%m%d', errors='coerce')
    else:
        normalized['date'] = pd.to_datetime(normalized['date'], errors='coerce')
    if 'daily_basic_trade_date' not in normalized.columns:
        normalized['daily_basic_trade_date'] = normalized['date']
    else:
        normalized['daily_basic_trade_date'] = pd.to_datetime(normalized['daily_basic_trade_date'], errors='coerce')

    for column in FUNDAMENTAL_DAILY_BASIC_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = None

    normalized = normalized.dropna(subset=['date']).sort_values('date')
    return normalized[['date', 'daily_basic_trade_date', *FUNDAMENTAL_DAILY_BASIC_COLUMNS]]


def normalize_fina_indicator_frame(fina_df):
    if fina_df is None or fina_df.empty:
        return empty_fina_indicator_frame()

    normalized = fina_df.copy()
    if 'ann_date' in normalized.columns:
        normalized['ann_date'] = pd.to_datetime(normalized['ann_date'], format='%Y%m%d', errors='coerce')
    else:
        normalized['ann_date'] = pd.to_datetime(normalized.get('available_date'), errors='coerce')

    if 'report_end_date' in normalized.columns:
        normalized['report_end_date'] = pd.to_datetime(normalized['report_end_date'], errors='coerce')
    else:
        normalized['report_end_date'] = pd.to_datetime(normalized.get('end_date'), format='%Y%m%d', errors='coerce')

    normalized = normalized.dropna(subset=['ann_date', 'report_end_date']).sort_values(['report_end_date', 'ann_date'])
    normalized = normalized.drop_duplicates(subset=['report_end_date'], keep='last').copy()

    normalized_roe = normalized.get('roe')
    if normalized_roe is None:
        normalized_roe = pd.Series([None] * len(normalized), index=normalized.index)
    normalized_roe = normalized_roe.map(normalize_rate)
    normalized['roe'] = normalized_roe

    previous_roe = None
    roe_qoq_values = []
    for value in normalized_roe.tolist():
        if previous_roe is None or value is None:
            roe_qoq_values.append(None)
        else:
            roe_qoq_values.append(value - previous_roe)
        previous_roe = value
    normalized['roe_qoq'] = roe_qoq_values
    normalized['available_date'] = normalized['ann_date']
    return normalized[['available_date', 'ann_date', 'report_end_date', 'roe', 'roe_qoq']].sort_values(
        ['available_date', 'report_end_date'],
        kind='mergesort',
    )


def materialize_fundamental_snapshot_frame(trading_dates, daily_df, fina_df):
    trading_dates = sorted(set(trading_dates))
    if not trading_dates:
        return pd.DataFrame(
            columns=[
                'date',
                'daily_basic_trade_date',
                *FUNDAMENTAL_DAILY_BASIC_COLUMNS,
                'available_date',
                'ann_date',
                'report_end_date',
                'roe',
                'roe_qoq',
            ]
        )

    merged = pd.DataFrame({'date': pd.to_datetime(trading_dates)}).sort_values('date')
    normalized_daily = normalize_daily_basic_frame(daily_df)
    normalized_fina = normalize_fina_indicator_frame(fina_df)

    if not normalized_daily.empty:
        merged = pd.merge_asof(merged, normalized_daily, on='date', direction='backward')
    else:
        merged['daily_basic_trade_date'] = pd.NaT
        for column in FUNDAMENTAL_DAILY_BASIC_COLUMNS:
            merged[column] = None

    if not normalized_fina.empty:
        merged = pd.merge_asof(
            merged,
            normalized_fina,
            left_on='date',
            right_on='available_date',
            direction='backward',
        )
    else:
        merged['available_date'] = pd.NaT
        merged['ann_date'] = pd.NaT
        merged['report_end_date'] = pd.NaT
        merged['roe'] = None
        merged['roe_qoq'] = None

    return merged


def materialize_fundamental_snapshot_rows(trading_dates, daily_df, fina_df):
    rows = []
    for row in materialize_fundamental_snapshot_frame(trading_dates, daily_df, fina_df).itertuples(index=False):
        daily_trade_date = getattr(row, 'daily_basic_trade_date', None)
        ann_date = getattr(row, 'ann_date', None)
        report_end_date = getattr(row, 'report_end_date', None)
        rows.append({
            'date': pd.Timestamp(row.date).date(),
            'daily_basic_trade_date': daily_trade_date.date() if pd.notna(daily_trade_date) else None,
            'fina_indicator_ann_date': ann_date.date() if pd.notna(ann_date) else None,
            'fina_indicator_end_date': report_end_date.date() if pd.notna(report_end_date) else None,
            'pe': safe_decimal(getattr(row, 'pe', None)),
            'pb': safe_decimal(getattr(row, 'pb', None)),
            'total_share': safe_decimal(getattr(row, 'total_share', None)),
            'float_share': safe_decimal(getattr(row, 'float_share', None)),
            'free_share': safe_decimal(getattr(row, 'free_share', None)),
            'total_mv': safe_decimal(getattr(row, 'total_mv', None)),
            'circ_mv': safe_decimal(getattr(row, 'circ_mv', None)),
            'roe': safe_decimal(getattr(row, 'roe', None)),
            'roe_qoq': safe_decimal(getattr(row, 'roe_qoq', None)),
        })
    return rows