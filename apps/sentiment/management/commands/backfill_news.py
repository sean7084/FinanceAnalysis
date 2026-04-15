from datetime import datetime, timedelta
from time import sleep

from django.core.management.base import BaseCommand, CommandError

from apps.sentiment.providers import DEFAULT_PROVIDER_NAMES, fetch_normalized_news_items
from apps.sentiment.tasks import (
    calculate_concept_heat,
    calculate_daily_sentiment,
    fetch_latest_market_news,
    ingest_latest_news,
)


class Command(BaseCommand):
    help = 'Fetch and ingest recent market news from configured providers.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--providers',
            default=','.join(DEFAULT_PROVIDER_NAMES),
            help='Comma-separated provider list. Supported: eastmoney,sina,tonghuashun',
        )
        parser.add_argument('--limit-per-provider', type=int, default=20)
        parser.add_argument('--start-at', help='Inclusive lower datetime bound, e.g. 2026-04-15 00:00:00')
        parser.add_argument('--end-at', help='Inclusive upper datetime bound, e.g. 2026-04-15 23:59:59')
        parser.add_argument('--chunk-days', type=int, default=30, help='Date-range fetch chunk size in days.')
        parser.add_argument('--sleep-seconds', type=float, default=0.2, help='Throttle delay between chunks.')
        parser.add_argument('--max-retries', type=int, default=4, help='Max retries per chunk when provider rate limit is hit.')
        parser.add_argument('--dry-run', action='store_true', help='Fetch and display rows without writing them.')
        parser.add_argument('--queue', action='store_true', help='Queue the fetch task via Celery instead of running inline.')
        parser.add_argument('--run-pipeline', action='store_true', help='Run daily sentiment and concept calculations after ingest.')

    @staticmethod
    def _parse_datetime(value, label):
        if not value:
            return None
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        raise CommandError(f'Invalid {label}: {value}. Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS')

    def handle(self, *args, **options):
        providers = [item.strip() for item in options['providers'].split(',') if item.strip()]
        if not providers:
            raise CommandError('At least one provider is required.')

        limit = int(options['limit_per_provider'])
        if limit < 0:
            raise CommandError('--limit-per-provider must be >= 0. Use 0 for unlimited.')

        start_at = options.get('start_at') or None
        end_at = options.get('end_at') or None
        chunk_days = max(1, int(options.get('chunk_days') or 30))
        sleep_seconds = max(0.0, float(options.get('sleep_seconds') or 0.0))
        max_retries = max(0, int(options.get('max_retries') or 0))

        if options['queue']:
            result = fetch_latest_market_news.delay(
                providers=providers,
                limit_per_provider=limit,
                start_at=start_at,
                end_at=end_at,
            )
            self.stdout.write(self.style.SUCCESS(f'Queued fetch_latest_market_news task: {result.id}'))
            return

        start_dt = self._parse_datetime(start_at, '--start-at') if start_at else None
        end_dt = self._parse_datetime(end_at, '--end-at') if end_at else None
        if start_dt and end_dt and start_dt > end_dt:
            raise CommandError('--start-at cannot be later than --end-at')

        chunks = []
        if start_dt and end_dt:
            cursor = start_dt
            while cursor <= end_dt:
                chunk_end = min(cursor + timedelta(days=chunk_days) - timedelta(seconds=1), end_dt)
                chunks.append((cursor, chunk_end))
                cursor = chunk_end + timedelta(seconds=1)
        else:
            chunks = [(start_dt, end_dt)]

        total_fetched = 0
        total_created_or_updated = 0
        preview_rows = []

        for idx, (chunk_start, chunk_end) in enumerate(chunks, start=1):
            chunk_start_str = chunk_start.strftime('%Y-%m-%d %H:%M:%S') if chunk_start else None
            chunk_end_str = chunk_end.strftime('%Y-%m-%d %H:%M:%S') if chunk_end else None

            attempt = 0
            while True:
                try:
                    items = fetch_normalized_news_items(
                        providers=providers,
                        limit_per_provider=limit,
                        start_at=chunk_start_str,
                        end_at=chunk_end_str,
                    )
                    break
                except Exception as exc:
                    message = str(exc)
                    if '每分钟最多访问该接口' not in message or attempt >= max_retries:
                        raise
                    attempt += 1
                    retry_wait = max(sleep_seconds, 35.0)
                    self.stdout.write(
                        self.style.WARNING(
                            f'Rate limit for chunk {idx}/{len(chunks)}; retry {attempt}/{max_retries} after {retry_wait:.0f}s.'
                        )
                    )
                    sleep(retry_wait)
            total_fetched += len(items)

            if options['dry_run']:
                preview_rows.extend(items[:3])
                self.stdout.write(
                    f'Chunk {idx}/{len(chunks)} fetched {len(items)} items '
                    f'({chunk_start_str or "latest"} -> {chunk_end_str or "latest"})'
                )
            else:
                ingest_message = ingest_latest_news(news_items=items)
                total_created_or_updated += len(items)
                self.stdout.write(
                    f'Chunk {idx}/{len(chunks)} fetched {len(items)} items '
                    f'({chunk_start_str or "latest"} -> {chunk_end_str or "latest"})'
                )
                self.stdout.write(self.style.SUCCESS(ingest_message))

            if sleep_seconds > 0 and idx < len(chunks):
                sleep(sleep_seconds)

        self.stdout.write(f'Total fetched: {total_fetched} items from providers: {", ".join(providers)}')

        if options['dry_run']:
            for item in preview_rows[:10]:
                self.stdout.write(f"- [{item['source']}] {item['published_at']} {item['title']}")
            return

        self.stdout.write(self.style.SUCCESS(f'Total ingested rows attempted: {total_created_or_updated}'))

        if options['run_pipeline']:
            sentiment_message = calculate_daily_sentiment()
            concept_message = calculate_concept_heat()
            self.stdout.write(self.style.SUCCESS(sentiment_message))
            self.stdout.write(self.style.SUCCESS(concept_message))