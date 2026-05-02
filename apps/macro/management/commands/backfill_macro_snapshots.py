from datetime import date
from time import sleep

import pandas as pd
import tushare as ts
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.date_floor import get_historical_data_floor
from apps.macro.models import MacroSnapshot
from apps.macro.providers import (
    DXY_TUSHARE_CODES,
    MACRO_FIELDS,
    _sleep_if_needed,
    build_monthly_yield_points,
    call_tushare_with_retries,
    extract_pmi_month,
    extract_fx_quote,
    fetch_macro_snapshot_from_akshare,
    normalize_dxy_quote,
    normalize_cny_usd_from_usd_quote,
)
from apps.macro.tasks import refresh_current_market_context


def _parse_date(value, name):
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise CommandError(f'Invalid {name}: {value}. Expected YYYY-MM-DD.') from exc


def _month_start_iter(start_date, end_date):
    cursor = start_date.replace(day=1)
    end_month = end_date.replace(day=1)
    while cursor <= end_month:
        yield cursor
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)


def _month_window_end(start_month, months_per_window):
    month_index = (start_month.year * 12 + start_month.month - 1) + max(int(months_per_window), 1) - 1
    year = month_index // 12
    month = month_index % 12 + 1
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - pd.Timedelta(days=1)


def _iter_fetch_windows(start_date, end_date, months_per_window=36):
    cursor = start_date.replace(day=1)
    final_date = end_date
    while cursor <= final_date:
        window_end = min(_month_window_end(cursor, months_per_window), final_date)
        yield cursor, window_end
        next_month = window_end.replace(day=1)
        if next_month.month == 12:
            cursor = date(next_month.year + 1, 1, 1)
        else:
            cursor = date(next_month.year, next_month.month + 1, 1)


def _to_decimal(value):
    if value in (None, '', 'nan'):
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        return str(value)
    except Exception:
        return None


def _next_month_start(month_date):
    if month_date.month == 12:
        return date(month_date.year + 1, 1, 1)
    return date(month_date.year, month_date.month + 1, 1)


def _month_start_from_token(token):
    value = str(token or '').strip()
    if len(value) < 6:
        return None
    try:
        return date(int(value[:4]), int(value[4:6]), 1)
    except ValueError:
        return None


def _yield_sleep_if_needed(delay_seconds):
    delay = float(delay_seconds or 0)
    if delay > 0:
        sleep(delay)


class Command(BaseCommand):
    help = 'Backfill MacroSnapshot monthly data from TuShare with AkShare fallback.'

    def add_arguments(self, parser):
        parser.add_argument('--start-date', default=get_historical_data_floor().isoformat())
        parser.add_argument('--end-date', default=date.today().isoformat())
        parser.add_argument('--disable-fallback', action='store_true')
        parser.add_argument('--resume-yields', action='store_true')

    def _yield_window_months(self):
        return max(int(getattr(settings, 'MACRO_YIELD_BACKFILL_WINDOW_MONTHS', 36) or 36), 1)

    def _yield_retry_attempts(self):
        return max(int(getattr(settings, 'MACRO_YIELD_BACKFILL_MAX_RETRIES', 3) or 3), 1)

    def _yield_retry_sleep_seconds(self):
        return float(getattr(settings, 'MACRO_YIELD_BACKFILL_RETRY_SLEEP_SECONDS', 65.0) or 65.0)

    def _yield_call_sleep_seconds(self):
        return float(getattr(settings, 'MACRO_YIELD_BACKFILL_CALL_SLEEP_SECONDS', 31.0) or 0)

    def _yield_resume_start(self, field_name, start_date, end_date, resume_yields):
        start_month = start_date.replace(day=1)
        if not resume_yields:
            return start_month

        last_completed = (
            MacroSnapshot.objects
            .filter(date__gte=start_month, date__lte=end_date.replace(day=1))
            .exclude(**{field_name: None})
            .order_by('-date')
            .values_list('date', flat=True)
            .first()
        )
        if last_completed is None:
            return start_month

        next_month = _next_month_start(last_completed)
        if next_month > end_date.replace(day=1):
            return None
        return max(next_month, start_month)

    def _persist_yield_points(self, field_name, retry_key, curve_term, points, retry_metadata):
        updated = 0
        retry_count = int((retry_metadata.get('retries') or {}).get(retry_key, 0) or 0)
        retry_messages = list((retry_metadata.get('retry_errors') or {}).get(retry_key, []))

        for month, point in points.items():
            month_date = _month_start_from_token(month)
            if month_date is None:
                continue

            snapshot, _created = MacroSnapshot.objects.get_or_create(date=month_date, defaults={'metadata': {}})
            metadata = dict(snapshot.metadata or {})
            yield_sources = dict(metadata.get('yield_sources') or {})
            yield_sources[field_name] = {
                'source': 'tushare_yc_cb',
                'curve_term': curve_term,
                'trade_date': point.get('trade_date'),
            }
            if point.get('curve_type'):
                yield_sources[field_name]['curve_type'] = point['curve_type']
            metadata['yield_sources'] = yield_sources

            retries = dict(metadata.get('retries') or {})
            if retry_count:
                retries[retry_key] = int(retries.get(retry_key, 0) or 0) + retry_count
            if retries:
                metadata['retries'] = retries

            retry_errors = dict(metadata.get('retry_errors') or {})
            if retry_messages:
                merged_messages = list(retry_errors.get(retry_key) or [])
                for message in retry_messages:
                    if message not in merged_messages:
                        merged_messages.append(message)
                retry_errors[retry_key] = merged_messages
            if retry_errors:
                metadata['retry_errors'] = retry_errors

            has_changed = getattr(snapshot, field_name) != point['yield'] or snapshot.metadata != metadata
            if not has_changed:
                continue

            setattr(snapshot, field_name, point['yield'])
            snapshot.metadata = metadata
            snapshot.save(update_fields=[field_name, 'metadata', 'updated_at'])
            updated += 1

        return updated

    def _backfill_tushare_yields(self, start_date, end_date, resume_yields=False):
        token = getattr(settings, 'TUSHARE_TOKEN', None)
        if not token:
            raise CommandError('TUSHARE_TOKEN is not configured.')

        pro = ts.pro_api(token)
        updated = 0
        windows_completed = 0

        for curve_term, field_name, retry_key in [
            (10, 'cn10y_yield', 'yield_10y'),
            (2, 'cn2y_yield', 'yield_2y'),
        ]:
            term_start = self._yield_resume_start(field_name, start_date, end_date, resume_yields)
            if term_start is None:
                continue

            for window_start, window_end in _iter_fetch_windows(
                term_start,
                end_date,
                months_per_window=self._yield_window_months(),
            ):
                retry_metadata = {'retries': {}, 'retry_errors': {}}
                yield_df = call_tushare_with_retries(
                    lambda curve_term=curve_term, window_start=window_start, window_end=window_end: pro.yc_cb(
                        curve_term=curve_term,
                        start_date=window_start.strftime('%Y%m%d'),
                        end_date=window_end.strftime('%Y%m%d'),
                        limit=2000,
                    ),
                    metadata=retry_metadata,
                    error_key=retry_key,
                    attempts=self._yield_retry_attempts(),
                    retry_sleep_seconds=self._yield_retry_sleep_seconds(),
                )
                _yield_sleep_if_needed(self._yield_call_sleep_seconds())

                monthly_points = build_monthly_yield_points(yield_df)
                filtered_points = {
                    month: point
                    for month, point in monthly_points.items()
                    if term_start.strftime('%Y%m') <= month <= end_date.strftime('%Y%m')
                }
                updated += self._persist_yield_points(field_name, retry_key, curve_term, filtered_points, retry_metadata)
                windows_completed += 1

        return updated, windows_completed

    def _fetch_tushare_monthly_maps(self, start_date, end_date):
        token = getattr(settings, 'TUSHARE_TOKEN', None)
        if not token:
            raise CommandError('TUSHARE_TOKEN is not configured.')

        pro = ts.pro_api(token)
        start_month = start_date.strftime('%Y%m')
        end_month = end_date.strftime('%Y%m')

        maps = {
            'cpi': {},
            'ppi': {},
            'pmi': {},
            'yield_2y': {},
            'yield_10y': {},
            'dxy': {},
            'cny_usd': {},
            'errors': {},
            'retries': {},
            'retry_errors': {},
        }

        try:
            cpi_df = call_tushare_with_retries(
                lambda: pro.cn_cpi(limit=5000),
                metadata=maps,
                error_key='cpi',
            )
            _sleep_if_needed()
            if cpi_df is not None and not cpi_df.empty:
                for row in cpi_df.to_dict(orient='records'):
                    month = str(row.get('month') or '')
                    if start_month <= month <= end_month:
                        maps['cpi'][month] = _to_decimal(row.get('nt_yoy'))
        except Exception as exc:
            maps['errors']['cpi'] = str(exc)

        try:
            ppi_df = call_tushare_with_retries(
                lambda: pro.cn_ppi(limit=5000),
                metadata=maps,
                error_key='ppi',
            )
            _sleep_if_needed()
            if ppi_df is not None and not ppi_df.empty:
                for row in ppi_df.to_dict(orient='records'):
                    month = str(row.get('month') or '')
                    if start_month <= month <= end_month:
                        maps['ppi'][month] = _to_decimal(row.get('ppi_yoy'))
        except Exception as exc:
            maps['errors']['ppi'] = str(exc)

        try:
            pmi_df = call_tushare_with_retries(
                lambda: pro.cn_pmi(limit=5000),
                metadata=maps,
                error_key='pmi',
            )
            _sleep_if_needed()
            if pmi_df is not None and not pmi_df.empty:
                for row in pmi_df.to_dict(orient='records'):
                    month_dt = extract_pmi_month(row)
                    month = month_dt.strftime('%Y%m') if month_dt is not None else ''
                    if start_month <= month <= end_month:
                        maps['pmi'][month] = {
                            'pmi_manufacturing': _to_decimal(row.get('PMI010000')),
                            'pmi_non_manufacturing': _to_decimal(row.get('PMI020100')),
                        }
        except Exception as exc:
            maps['errors']['pmi'] = str(exc)

        fx_specs = [
            ('dxy', DXY_TUSHARE_CODES, normalize_dxy_quote),
            ('cny_usd', ['USDCNH.FXCM'], normalize_cny_usd_from_usd_quote),
        ]
        for key, ts_codes, normalizer in fx_specs:
            field_errors = []
            for ts_code in ts_codes:
                try:
                    for window_start, window_end in _iter_fetch_windows(start_date, end_date, months_per_window=36):
                        fx_df = call_tushare_with_retries(
                            lambda ts_code=ts_code, window_start=window_start, window_end=window_end: pro.fx_daily(
                                ts_code=ts_code,
                                start_date=window_start.strftime('%Y%m%d'),
                                end_date=window_end.strftime('%Y%m%d'),
                                limit=2000,
                            ),
                            metadata=maps,
                            error_key=f'{key}_{ts_code}',
                        )
                        _sleep_if_needed()
                        if fx_df is not None and not fx_df.empty:
                            fx_sorted = fx_df.sort_values('trade_date')
                            for row in fx_sorted.to_dict(orient='records'):
                                month = str(row.get('trade_date') or '')[:6]
                                if start_month <= month <= end_month:
                                    close_value = extract_fx_quote(row)
                                    normalized_value = normalizer(close_value)
                                    if normalized_value is not None:
                                        maps[key][month] = _to_decimal(normalized_value)
                except Exception as exc:
                    field_errors.append(f'{ts_code}: {exc}')

            if not maps[key] and field_errors:
                maps['errors'][key] = '; '.join(field_errors)

        return maps

    def handle(self, *args, **options):
        floor_date = get_historical_data_floor()
        start_date = max(_parse_date(options['start_date'], 'start-date'), floor_date)
        end_date = _parse_date(options['end_date'], 'end-date')
        if end_date < start_date:
            raise CommandError('end-date must be on or after start-date.')

        maps = self._fetch_tushare_monthly_maps(start_date, end_date)

        created_count = 0
        updated_count = 0
        fallback_count = 0
        yield_updated_count = 0
        yield_window_count = 0
        latest_requested_month = end_date.replace(day=1)

        for month_date in _month_start_iter(start_date, end_date):
            month = month_date.strftime('%Y%m')
            existing = MacroSnapshot.objects.filter(date=month_date).first()
            existing_metadata = dict(existing.metadata or {}) if existing else {}
            preserved_retries = {
                key: value
                for key, value in dict(existing_metadata.get('retries') or {}).items()
                if str(key).startswith('yield_')
            }
            preserved_retry_errors = {
                key: value
                for key, value in dict(existing_metadata.get('retry_errors') or {}).items()
                if str(key).startswith('yield_')
            }
            metadata = dict(existing_metadata)
            metadata.pop('fallback_source', None)
            metadata.pop('fallback_fields', None)
            metadata.pop('fallback_payload', None)
            metadata['source'] = 'tushare'
            metadata['month'] = month
            metadata['errors'] = maps['errors']
            retries = dict(preserved_retries)
            retries.update(maps['retries'])
            metadata['retries'] = retries
            retry_errors = dict(preserved_retry_errors)
            retry_errors.update(maps['retry_errors'])
            metadata['retry_errors'] = retry_errors

            defaults = {
                'dxy': maps['dxy'].get(month),
                'cny_usd': maps['cny_usd'].get(month),
                'pmi_manufacturing': (maps['pmi'].get(month) or {}).get('pmi_manufacturing'),
                'pmi_non_manufacturing': (maps['pmi'].get(month) or {}).get('pmi_non_manufacturing'),
                'cpi_yoy': maps['cpi'].get(month),
                'ppi_yoy': maps['ppi'].get(month),
                'metadata': metadata,
            }

            missing_fields = [field for field in MACRO_FIELDS if defaults.get(field) is None]
            if missing_fields and not options['disable_fallback'] and month_date == latest_requested_month:
                fallback_payload = fetch_macro_snapshot_from_akshare(snapshot_date=month_date) or {}
                fallback_count += 1
                filled_fields = []
                for field in missing_fields:
                    if defaults.get(field) is None and fallback_payload.get(field) is not None:
                        defaults[field] = fallback_payload[field]
                        filled_fields.append(field)
                if filled_fields:
                    defaults['metadata']['fallback_source'] = 'akshare'
                    defaults['metadata']['fallback_fields'] = filled_fields
                    defaults['metadata']['fallback_payload'] = fallback_payload.get('metadata', {})

            if existing is None:
                MacroSnapshot.objects.create(date=month_date, **defaults)
                created_count += 1
            else:
                for field, value in defaults.items():
                    setattr(existing, field, value)
                existing.save()
                updated_count += 1

        yield_updated_count, yield_window_count = self._backfill_tushare_yields(
            start_date,
            end_date,
            resume_yields=options['resume_yields'],
        )

        latest = MacroSnapshot.objects.order_by('-date').first()
        latest_id = getattr(latest, 'id', None) if latest is not None else None
        if latest_id is not None:
            refresh_current_market_context(snapshot_id=latest_id)

        self.stdout.write(self.style.SUCCESS(
            f'MacroSnapshot backfill completed: created={created_count}, updated={updated_count}, yield_updates={yield_updated_count}, yield_windows={yield_window_count}, fallback_used={fallback_count}, range={start_date}..{end_date}'
        ))
