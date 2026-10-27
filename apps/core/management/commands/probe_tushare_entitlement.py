"""Probe which TuShare endpoints this token may actually call.

Entitlement is the first thing a data plan needs and the last thing the code records: the
``yc_cb`` dead end was discovered after a backfill had been scheduled. This command calls a
tiny, fixed query per interface, classifies the outcome (``ok`` / ``ok-empty`` / ``denied`` /
``rate-limited`` / ``error``) and writes a dated markdown sheet. It writes no database row.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.core import tushare_client

INTERFACE_QUERIES = {
    'fund_adj': lambda pro, start, end: pro.fund_adj(ts_code='510300.SH', start_date=start, end_date=end),
    'fund_div': lambda pro, start, end: pro.fund_div(ts_code='510300.SH', start_date=start, end_date=end),
    'fund_share': lambda pro, start, end: pro.fund_share(ts_code='510300.SH', start_date=start, end_date=end),
    'index_dailybasic': lambda pro, start, end: pro.index_dailybasic(ts_code='000300.SH', start_date=start, end_date=end),
    'index_weight': lambda pro, start, end: pro.index_weight(index_code='000300.SH', start_date=start, end_date=end),
    'yc_cb': lambda pro, start, end: pro.yc_cb(ts_code='1001.CB', curve_type='0', start_date=start, end_date=end),
    'shibor': lambda pro, start, end: pro.shibor(start_date=start, end_date=end),
    'monetary_policy': lambda pro, start, end: pro.monetary_policy(start_date=start, end_date=end),
}


def classify(exc: Exception | None, rows: int | None) -> str:
    """The five outcomes, by the provider's own words rather than by HTTP status."""
    if exc is None:
        return 'ok' if rows else 'ok-empty'
    message = str(exc)
    if '频率超限' in message:
        return 'rate-limited'
    if '权限' in message or 'permission' in message.lower() or '40203' in message:
        return 'denied'
    return 'error'


def probe(pro, interfaces, *, start: str, end: str) -> list[dict]:
    results = []
    for name in interfaces:
        fn = INTERFACE_QUERIES.get(name)
        if fn is None:
            results.append({'interface': name, 'status': 'unknown', 'rows': None,
                            'message': 'no probe query defined'})
            continue
        exc = None
        rows = None
        try:
            frame = fn(pro, start, end)
            rows = 0 if frame is None else int(len(frame))
        except Exception as caught:  # noqa: BLE001 - the point is to classify the failure
            exc = caught
        results.append({
            'interface': name,
            'status': classify(exc, rows),
            'rows': rows,
            'message': '' if exc is None else str(exc)[:300],
        })
    return results


class Command(BaseCommand):
    help = 'Probe TuShare endpoint entitlement and write a dated markdown sheet.'

    def add_arguments(self, parser):
        parser.add_argument('--interfaces', default=','.join(INTERFACE_QUERIES),
                            help='Comma-separated interface names to probe.')
        parser.add_argument('--out', default=None,
                            help='Report path (default docs/reports/tushare-entitlement-<date>.md).')
        parser.add_argument('--days', type=int, default=10,
                            help='Look-back window for the probe query (default 10).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print the table without writing the report.')

    def handle(self, *args, **options):
        interfaces = [item.strip() for item in options['interfaces'].split(',') if item.strip()]
        end = date.today()
        start = end - timedelta(days=max(1, int(options['days'])))
        results = probe(
            tushare_client.pro(), interfaces,
            start=start.strftime('%Y%m%d'), end=end.strftime('%Y%m%d'),
        )
        lines = [
            f'# TuShare entitlement probe ({end.isoformat()})',
            '',
            f'Window: {start.isoformat()}..{end.isoformat()}. One tiny query per interface.',
            '',
            '| interface | status | rows | message |',
            '| :--- | :--- | ---: | :--- |',
        ]
        for row in results:
            rows = '-' if row['rows'] is None else str(row['rows'])
            lines.append(f"| {row['interface']} | {row['status']} | {rows} | {row['message']} |")
        report = '\n'.join(lines) + '\n'
        self.stdout.write(report)
        if options['dry_run']:
            return
        out = options['out'] or os.path.join(
            str(settings.BASE_DIR), 'docs', 'reports', f'tushare-entitlement-{end.isoformat()}.md',
        )
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'w', encoding='utf-8') as handle:
            handle.write(report)
        self.stdout.write(f'written: {out}')
