import hashlib
from datetime import datetime, time

import akshare as ak
import tushare as ts
from django.conf import settings
from django.utils import timezone


DEFAULT_PROVIDER_NAMES = ('eastmoney', 'sina', 'tonghuashun')

PROVIDER_TO_SOURCE = {
    'eastmoney': 'EASTMONEY',
    'sina': 'SINA',
    'tonghuashun': 'TONGHUASHUN',
}


def _coerce_datetime(value, provider_name):
    if value is None:
        return timezone.now()

    if hasattr(value, 'to_pydatetime'):
        value = value.to_pydatetime()

    if isinstance(value, datetime):
        dt = value
    elif hasattr(value, 'year') and hasattr(value, 'month') and hasattr(value, 'day') and hasattr(value, 'hour'):
        dt = datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)
    elif hasattr(value, 'year') and hasattr(value, 'month') and hasattr(value, 'day') and not isinstance(value, str):
        dt = datetime.combine(value, time.min)
    elif isinstance(value, str):
        cleaned = value.strip()
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d', '%Y%m%d%H%M%S', '%Y%m%d'):
            try:
                dt = datetime.strptime(cleaned, fmt)
                break
            except ValueError:
                dt = None
        if dt is None:
            dt = timezone.now()
    else:
        dt = timezone.now()

    if timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt


def _synthetic_url(provider_name, payload_text):
    digest = hashlib.sha256(payload_text.encode('utf-8')).hexdigest()
    return f'https://synthetic.financeanalysis.local/{provider_name}/{digest}'


def _normalize_eastmoney(record):
    title = str(record.get('标题') or '').strip()
    summary = str(record.get('摘要') or record.get('摘 要') or '').strip()
    published_at = _coerce_datetime(record.get('发布时间'), 'eastmoney')
    url = str(record.get('链接') or '').strip() or _synthetic_url('eastmoney', f'{title}|{summary}|{published_at.isoformat()}')
    return {
        'source': PROVIDER_TO_SOURCE['eastmoney'],
        'title': title,
        'summary': summary,
        'content': summary,
        'url': url,
        'published_at': published_at,
        'language': 'zh',
        'concept_tags': [],
        'metadata': {
            'provider': 'eastmoney',
            'raw': {
                'title': title,
                'summary': summary,
                'published_at': str(record.get('发布时间') or ''),
                'url': str(record.get('链接') or ''),
            },
        },
    }


def _normalize_sina(record):
    content = str(record.get('内容') or '').strip()
    published_at = _coerce_datetime(record.get('时间'), 'sina')
    title = content[:80] if content else 'Sina news item'
    url = _synthetic_url('sina', f'{content}|{published_at.isoformat()}')
    return {
        'source': PROVIDER_TO_SOURCE['sina'],
        'title': title,
        'summary': content[:240],
        'content': content,
        'url': url,
        'published_at': published_at,
        'language': 'zh',
        'concept_tags': [],
        'metadata': {
            'provider': 'sina',
            'synthetic_url': True,
            'raw': {
                'time': str(record.get('时间') or ''),
                'content': content,
            },
        },
    }


def _normalize_tonghuashun(record):
    title = str(record.get('标题') or '').strip()
    content = str(record.get('内容') or '').strip()
    published_at = _coerce_datetime(record.get('发布时间'), 'tonghuashun')
    url = str(record.get('链接') or '').strip() or _synthetic_url('tonghuashun', f'{title}|{content}|{published_at.isoformat()}')
    return {
        'source': PROVIDER_TO_SOURCE['tonghuashun'],
        'title': title,
        'summary': content[:240],
        'content': content,
        'url': url,
        'published_at': published_at,
        'language': 'zh',
        'concept_tags': [],
        'metadata': {
            'provider': 'tonghuashun',
            'raw': {
                'title': title,
                'content': content,
                'published_at': str(record.get('发布时间') or ''),
                'url': str(record.get('链接') or ''),
            },
        },
    }


def _normalize_tushare_major(record):
    title = str(record.get('title') or '').strip()
    content = str(record.get('content') or '').strip()
    src = str(record.get('src') or '').strip().lower()
    published_at = _coerce_datetime(record.get('pub_time'), 'tushare_major')
    if src == 'sina':
        source = PROVIDER_TO_SOURCE['sina']
    elif src in {'10jqka', 'tonghuashun'}:
        source = PROVIDER_TO_SOURCE['tonghuashun']
    elif src == 'eastmoney':
        source = PROVIDER_TO_SOURCE['eastmoney']
    else:
        source = 'OTHER'

    url = str(record.get('url') or '').strip() or _synthetic_url('tushare_major', f'{title}|{content}|{published_at.isoformat()}')
    return {
        'source': source,
        'title': title or content[:80] or 'Major news item',
        'summary': content[:240],
        'content': content,
        'url': url,
        'published_at': published_at,
        'language': 'zh',
        'concept_tags': [],
        'metadata': {
            'provider': 'tushare_major',
            'provider_src': src,
            'raw': {
                'title': title,
                'pub_time': str(record.get('pub_time') or ''),
                'src': src,
                'url': str(record.get('url') or ''),
            },
        },
    }


def _normalize_tushare_cctv(record):
    title = str(record.get('title') or '').strip()
    content = str(record.get('content') or '').strip()
    published_at = _coerce_datetime(record.get('date'), 'tushare_cctv')
    url = _synthetic_url('tushare_cctv', f'{title}|{content}|{published_at.isoformat()}')
    return {
        'source': 'OTHER',
        'title': title or content[:80] or 'CCTV news item',
        'summary': content[:240],
        'content': content,
        'url': url,
        'published_at': published_at,
        'language': 'zh',
        'concept_tags': [],
        'metadata': {
            'provider': 'tushare_cctv',
            'synthetic_url': True,
            'raw': {
                'date': str(record.get('date') or ''),
                'title': title,
            },
        },
    }


def fetch_provider_records(provider_name, limit_per_provider=50, start_at=None, end_at=None):
    provider = provider_name.strip().lower()
    start_date = _coerce_datetime(start_at, provider).strftime('%Y%m%d') if start_at else None
    end_date = _coerce_datetime(end_at, provider).strftime('%Y%m%d') if end_at else None

    if provider == 'eastmoney':
        frame = ak.stock_info_global_em()
    elif provider == 'sina':
        frame = ak.stock_info_global_sina()
    elif provider in {'tonghuashun', 'ths'}:
        provider = 'tonghuashun'
        frame = ak.stock_info_global_ths()
    elif provider in {'tushare_major', 'major', 'major_news'}:
        provider = 'tushare_major'
        token = getattr(settings, 'TUSHARE_TOKEN', None)
        if not token:
            raise ValueError('TUSHARE_TOKEN is required for tushare_major provider.')
        pro = ts.pro_api(token)
        kwargs = {}
        if start_date:
            kwargs['start_date'] = start_date
        if end_date:
            kwargs['end_date'] = end_date
        frame = pro.major_news(**kwargs)
    elif provider in {'tushare_cctv', 'cctv', 'cctv_news'}:
        provider = 'tushare_cctv'
        token = getattr(settings, 'TUSHARE_TOKEN', None)
        if not token:
            raise ValueError('TUSHARE_TOKEN is required for tushare_cctv provider.')
        pro = ts.pro_api(token)
        kwargs = {}
        if start_date:
            kwargs['start_date'] = start_date
        if end_date:
            kwargs['end_date'] = end_date
        frame = pro.cctv_news(**kwargs)
    else:
        raise ValueError(f'Unsupported provider: {provider_name}')

    if frame is None or frame.empty:
        return provider, []

    if limit_per_provider and int(limit_per_provider) > 0:
        frame = frame.head(int(limit_per_provider))

    return provider, frame.to_dict(orient='records')


def normalize_provider_record(provider_name, record):
    provider = provider_name.strip().lower()
    if provider == 'eastmoney':
        return _normalize_eastmoney(record)
    if provider == 'sina':
        return _normalize_sina(record)
    if provider in {'tonghuashun', 'ths'}:
        return _normalize_tonghuashun(record)
    if provider in {'tushare_major', 'major', 'major_news'}:
        return _normalize_tushare_major(record)
    if provider in {'tushare_cctv', 'cctv', 'cctv_news'}:
        return _normalize_tushare_cctv(record)
    raise ValueError(f'Unsupported provider: {provider_name}')


def fetch_normalized_news_items(providers=None, limit_per_provider=50, start_at=None, end_at=None):
    provider_names = providers or DEFAULT_PROVIDER_NAMES
    start_dt = _coerce_datetime(start_at, 'filter') if start_at else None
    end_dt = _coerce_datetime(end_at, 'filter') if end_at else None

    items = []
    seen_urls = set()

    for provider_name in provider_names:
        provider, records = fetch_provider_records(
            provider_name,
            limit_per_provider=limit_per_provider,
            start_at=start_at,
            end_at=end_at,
        )
        for record in records:
            item = normalize_provider_record(provider, record)
            published_at = item['published_at']
            if start_dt and published_at < start_dt:
                continue
            if end_dt and published_at > end_dt:
                continue
            if item['url'] in seen_urls:
                continue
            seen_urls.add(item['url'])
            items.append(item)

    items.sort(key=lambda item: item['published_at'], reverse=True)
    return items