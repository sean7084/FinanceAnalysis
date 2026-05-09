import csv
import re
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.markets.models import Asset, AssetSuspension, OHLCV


DETAILS_PATTERN = re.compile(
    r':\s*(?P<start>\d{4}-\d{2}-\d{2})\.\.(?P<end>\d{4}-\d{2}-\d{2})\s*\((?P<count>\d+)\s+rows\)\.?'
)


def _get_akshare_module():
    try:
        import akshare as ak
    except ImportError:
        return None
    return ak


def _parse_details_window(details):
    match = DETAILS_PATTERN.search(str(details or ''))
    if match is None:
        raise CommandError(f'Unable to parse suspension details: {details}')
    return (
        date.fromisoformat(match.group('start')),
        date.fromisoformat(match.group('end')),
        int(match.group('count')),
    )


def _split_ts_code(ts_code):
    token = str(ts_code or '').strip().upper()
    if '.' not in token:
        raise CommandError(f'Invalid ts_code: {ts_code}')
    symbol, suffix = token.split('.', 1)
    return symbol.zfill(6), suffix


def _build_baidu_headers():
    return {
        'accept': 'application/vnd.finance-web.v1+json',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'en,zh-CN;q=0.9,zh;q=0.8',
        'cache-control': 'no-cache',
        'origin': 'https://finance.baidu.com',
        'pragma': 'no-cache',
        'priority': 'u=1, i',
        'referer': 'https://finance.baidu.com/',
        'user-agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/142.0.0.0 Safari/537.36'
        ),
    }


class Command(BaseCommand):
    help = 'Verify OHLCV/full-day suspension overlaps against AkShare Baidu suspension notices and optionally delete confirmed OHLCV rows.'

    def add_arguments(self, parser):
        parser.add_argument('--csv-file', default='')
        parser.add_argument('--symbols', default='')
        parser.add_argument('--output-file', default='')
        parser.add_argument('--baidu-cookie', default='')
        parser.add_argument('--execute', action='store_true')

    def handle(self, *args, **options):
        csv_file = str(options.get('csv_file') or '').strip()
        if not csv_file:
            raise CommandError('csv-file is required.')

        ak = _get_akshare_module()
        if ak is None:
            raise CommandError('akshare is not installed in the current environment.')

        selected_symbols = {
            token.strip().upper()
            for token in str(options.get('symbols') or '').split(',')
            if token.strip()
        }
        report_rows = self._load_report_rows(csv_file, selected_symbols)
        if not report_rows:
            raise CommandError('No ohlcv_on_full_day_suspension rows were found for the requested csv-file selection.')

        assets_by_ts_code = Asset.objects.in_bulk(
            [row['asset_ts_code'] for row in report_rows],
            field_name='ts_code',
        )

        pending_rows = []
        dates_to_fetch = set()
        results = []
        mismatch_rows = 0

        for report_row in report_rows:
            asset = assets_by_ts_code.get(report_row['asset_ts_code'])
            if asset is None:
                results.append({
                    'asset_symbol': report_row['asset_symbol'],
                    'asset_ts_code': report_row['asset_ts_code'],
                    'asset_name': report_row['asset_name'],
                    'window_start': report_row['window_start'].isoformat(),
                    'window_end': report_row['window_end'].isoformat(),
                    'expected_overlap_count': report_row['expected_overlap_count'],
                    'db_overlap_count': 0,
                    'trade_date': '',
                    'akshare_rows_on_date': '',
                    'akshare_verified': False,
                    'action': 'skipped',
                    'deleted_rows': 0,
                    'reason': 'asset_missing_in_database',
                    'akshare_error': '',
                    'matched_suspend_start': '',
                    'matched_resume_end': '',
                    'matched_suspend_reason': '',
                })
                continue

            overlap_dates = self._reconstruct_overlap_dates(
                asset,
                report_row['window_start'],
                report_row['window_end'],
            )
            if len(overlap_dates) != report_row['expected_overlap_count']:
                mismatch_rows += 1
                results.append({
                    'asset_symbol': asset.symbol,
                    'asset_ts_code': asset.ts_code,
                    'asset_name': asset.name,
                    'window_start': report_row['window_start'].isoformat(),
                    'window_end': report_row['window_end'].isoformat(),
                    'expected_overlap_count': report_row['expected_overlap_count'],
                    'db_overlap_count': len(overlap_dates),
                    'trade_date': '',
                    'akshare_rows_on_date': '',
                    'akshare_verified': False,
                    'action': 'skipped',
                    'deleted_rows': 0,
                    'reason': 'db_overlap_count_mismatch',
                    'akshare_error': '',
                    'matched_suspend_start': '',
                    'matched_resume_end': '',
                    'matched_suspend_reason': '',
                })
                continue

            pending_rows.append((asset, report_row, overlap_dates))
            dates_to_fetch.update(overlap_dates)

        baidu_cookie = str(options.get('baidu_cookie') or '').strip() or None
        akshare_rows_by_date = self._fetch_akshare_rows_by_date(ak, sorted(dates_to_fetch), baidu_cookie)
        fetch_errors = sum(1 for entry in akshare_rows_by_date.values() if entry['error'])

        verified_dates = 0
        deleted_rows = 0
        for asset, report_row, overlap_dates in pending_rows:
            asset_code, asset_exchange = _split_ts_code(asset.ts_code)
            for overlap_date in overlap_dates:
                akshare_entry = akshare_rows_by_date.get(
                    overlap_date,
                    {'rows': [], 'error': 'missing_akshare_fetch_result'},
                )
                akshare_rows = akshare_entry['rows']
                matched_row = self._match_akshare_row(akshare_rows, asset_code, asset_exchange)
                is_verified = matched_row is not None
                action = 'kept'
                deleted_count = 0
                if is_verified:
                    verified_dates += 1
                    if options.get('execute'):
                        deleted_count, _ = OHLCV.objects.filter(asset=asset, date=overlap_date).delete()
                        deleted_rows += deleted_count
                        action = 'deleted' if deleted_count else 'not_found'
                    else:
                        action = 'would_delete'

                results.append({
                    'asset_symbol': asset.symbol,
                    'asset_ts_code': asset.ts_code,
                    'asset_name': asset.name,
                    'window_start': report_row['window_start'].isoformat(),
                    'window_end': report_row['window_end'].isoformat(),
                    'expected_overlap_count': report_row['expected_overlap_count'],
                    'db_overlap_count': len(overlap_dates),
                    'trade_date': overlap_date.isoformat(),
                    'akshare_rows_on_date': len(akshare_rows),
                    'akshare_verified': is_verified,
                    'action': action,
                    'deleted_rows': deleted_count,
                    'reason': self._result_reason(akshare_entry, is_verified),
                    'akshare_error': akshare_entry['error'],
                    'matched_suspend_start': matched_row.get('停牌时间', '') if matched_row else '',
                    'matched_resume_end': matched_row.get('复牌时间', '') if matched_row else '',
                    'matched_suspend_reason': matched_row.get('停牌事项说明', '') if matched_row else '',
                })

        output_file = Path(str(options.get('output_file') or '').strip() or self._default_output_file())
        output_file.parent.mkdir(parents=True, exist_ok=True)
        self._write_results(output_file, results)

        self.stdout.write(self.style.SUCCESS(
            'Suspension/OHLCV reconciliation complete: '
            f'report_rows={len(report_rows)}, '
            f'mismatch_rows={mismatch_rows}, '
            f'overlap_dates_checked={sum(1 for row in results if row["trade_date"])}, '
            f'fetch_errors={fetch_errors}, '
            f'verified_dates={verified_dates}, '
            f'deleted_rows={deleted_rows}, '
            f'output_file={output_file}'
        ))

    def _default_output_file(self):
        stamp = timezone.now().strftime('%Y%m%d_%H%M%S')
        return Path('reports') / f'suspension_overlap_reconciliation_{stamp}.csv'

    def _load_report_rows(self, csv_file, selected_symbols):
        csv_path = Path(str(csv_file)).expanduser()
        if not csv_path.exists():
            raise CommandError(f'csv-file does not exist: {csv_file}')

        rows = []
        with csv_path.open(newline='', encoding='utf-8') as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if row.get('issue_type') != 'ohlcv_on_full_day_suspension':
                    continue

                asset_symbol = str(row.get('asset_symbol') or '').strip().upper()
                asset_ts_code = str(row.get('asset_ts_code') or '').strip().upper()
                if selected_symbols and asset_symbol not in selected_symbols and asset_ts_code not in selected_symbols:
                    continue

                window_start, window_end, expected_overlap_count = _parse_details_window(row.get('details'))
                rows.append({
                    'asset_symbol': asset_symbol,
                    'asset_ts_code': asset_ts_code,
                    'asset_name': str(row.get('asset_name') or '').strip(),
                    'window_start': window_start,
                    'window_end': window_end,
                    'expected_overlap_count': expected_overlap_count,
                })
        return rows

    def _reconstruct_overlap_dates(self, asset, window_start, window_end):
        suspension_dates = set(
            AssetSuspension.objects.filter(
                asset=asset,
                is_full_day=True,
                trade_date__gte=window_start,
                trade_date__lte=window_end,
            ).values_list('trade_date', flat=True)
        )
        ohlcv_dates = set(
            OHLCV.objects.filter(
                asset=asset,
                date__gte=window_start,
                date__lte=window_end,
            ).values_list('date', flat=True)
        )
        return sorted(suspension_dates.intersection(ohlcv_dates))

    def _fetch_akshare_rows_by_date(self, ak, overlap_dates, baidu_cookie):
        rows_by_date = {}
        resolved_cookie = baidu_cookie
        cookie_error = ''
        if resolved_cookie is None:
            resolved_cookie, cookie_error = self._resolve_baidu_cookie(ak)

        for overlap_date in overlap_dates:
            if cookie_error:
                rows_by_date[overlap_date] = {'rows': [], 'error': cookie_error}
                continue

            kwargs = {'date': overlap_date.strftime('%Y%m%d')}
            if resolved_cookie:
                kwargs['cookie'] = resolved_cookie
            try:
                frame = ak.news_trade_notify_suspend_baidu(**kwargs)
            except Exception as exc:
                rows_by_date[overlap_date] = {'rows': [], 'error': str(exc)}
                continue
            rows_by_date[overlap_date] = {
                'rows': frame.to_dict(orient='records') if frame is not None and not frame.empty else [],
                'error': '',
            }
        return rows_by_date

    def _resolve_baidu_cookie(self, ak):
        cookie_getter = ak.news_trade_notify_suspend_baidu.__globals__.get('_get_baidu_cookie')
        if cookie_getter is None:
            return None, ''
        try:
            return cookie_getter(_build_baidu_headers()), ''
        except Exception as exc:
            return None, str(exc)

    def _match_akshare_row(self, akshare_rows, asset_code, asset_exchange):
        for row in akshare_rows:
            if str(row.get('股票代码') or '').strip().zfill(6) != asset_code:
                continue
            if str(row.get('交易所代码') or '').strip().upper() != asset_exchange:
                continue
            return row
        return None

    def _result_reason(self, akshare_entry, is_verified):
        if akshare_entry['error']:
            return 'akshare_fetch_error'
        if is_verified:
            return 'akshare_match'
        akshare_rows = akshare_entry['rows']
        if not akshare_rows:
            return 'no_akshare_rows_for_date'
        return 'asset_not_present_in_akshare_date_rows'

    def _write_results(self, output_file, rows):
        fieldnames = [
            'asset_symbol',
            'asset_ts_code',
            'asset_name',
            'window_start',
            'window_end',
            'expected_overlap_count',
            'db_overlap_count',
            'trade_date',
            'akshare_rows_on_date',
            'akshare_verified',
            'action',
            'deleted_rows',
            'reason',
            'akshare_error',
            'matched_suspend_start',
            'matched_resume_end',
            'matched_suspend_reason',
        ]
        with output_file.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)