"""ETF universe classification and point-in-time pool eligibility.

TuShare's ``fund_basic`` has no field that says "this is an index ETF in sector X".
Its ``fund_type`` column only ever holds 股票型/混合型/债券型/REITs/其他/货币型, so
the classification has to be assembled from three places in priority order:
``invest_type`` (which does distinguish 被动指数型 from 黄金现货合约), then
``benchmark`` (the tracked-index description, i.e. what the module treats as
``track_index``), then ``name`` keywords. Roughly 467 rows have a blank
``invest_type``, so the name/benchmark tiers are load-bearing rather than optional.
"""

import re
from datetime import timedelta

from django.db.models import Q
from django.db.models.functions import Coalesce

from .models import ETFCategory, ETFUniverse


# On-exchange vehicles that are not ETFs. REITs are a separate product with its own
# trading rules; the active/mixed types are 场内LOF style funds whose NAV mechanics do
# not match an index-tracking ETF's intraday pricing.
EXCLUDED_FUND_TYPES = frozenset({'REITs'})
EXCLUDED_INVEST_TYPES = frozenset({'混合型', '灵活配置型', '平衡型', '成长型'})
EXCLUDED_NAME_TOKENS = ('LOF', '分级', '定开', '封闭')

# ``EXCLUDED_NAME_TOKENS`` matches nothing in practice: TuShare fund names are the
# brand+index string ("国泰国证房地产行业指数-A") and never carry the product type. The
# exchange code prefix is the only field that distinguishes a genuine ETF from an
# on-exchange LOF or a 分级 share, so the exclusion keys on it.
#   SZ 159xxx      -- ETF                                    (must stay rankable)
#   SZ 150/151/152 -- 分级 structured shares of a LOF parent
#   SZ 16xxxx      -- LOF
#   SH 50xxxx      -- LOF (500/501/502/505/506)
#   SH 51/52/56/57/58xxxx -- ETF
EXCLUDED_CODE_PREFIXES = {
    'SZ': ('16', '150', '151', '152'),
    'SH': ('50',),
}

INDEX_INVEST_TYPES = frozenset({'被动指数型', '增强指数型'})

# ``现金`` on its own matched 33 equity index funds -- every one of them a 自由现金流
# (free cash flow) ETF, which is a dividend-style equity screen, not a cash vehicle. The
# keyword stays because the real money funds need it (添富现金ETF, 现金宝), and the collision
# is cut at the compound instead: ``现金流`` disqualifies a name from the money tier.
MONEY_KEYWORDS = ('货币', '现金', '理财金', '快线')
MONEY_EXCLUDE_KEYWORDS = ('现金流',)
# ``有色`` and ``石化`` are deliberately absent: they name equity sectors (有色金属ETF,
# 石化ETF) whose benchmarks also contain them. Only physical-commodity vehicles belong.
COMMODITY_KEYWORDS = (
    '黄金', '上海金', '白银', '原油', '豆粕', '商品', '能化', '天然气',
)
BOND_KEYWORDS = (
    '国债', '政金债', '信用债', '公司债', '企业债', '可转债', '城投', '债券', '债ETF',
    '中短债', '利率债', '浮息债',
)
QDII_KEYWORDS = (
    'QDII', '纳斯达克', '纳指', '标普', '道琼斯', '美国', '全球', '亚太', '港股', '恒生',
    'H股', '中概', '德国', '法国', '英国', '日本', '日经', '东证', '印度', '越南', '沙特',
    '东盟', '中韩', '欧洲', '海外', '原油', '富时', 'MSCI', '境外',
)
# ``收益`` is not a keyword here. TuShare writes every index benchmark as
# "...指数收益率×100%", so matching it classified the entire passive pool as dividend.
DIVIDEND_KEYWORDS = ('红利', '股息', '分红')
GROWTH_KEYWORDS = ('成长', '创业板', '科创', '创新', '新兴', '科技')

BROAD_KEYWORDS = (
    '沪深300', '中证500', '中证1000', '中证A500', '中证200', '中证800', '上证50',
    '深证100', '创业板指数', '创业板50', '科创50', '科创板50', 'MSCI中国', '中证100',
    '上证180', '中证全指', '国证2000', '中证500增强', '沪深300增强',
)

# Sector buckets, checked in order. Each entry maps a category to the keywords that
# imply it, matched against ``name`` and ``benchmark`` together.
SECTOR_KEYWORDS = (
    (
        ETFCategory.SECTOR_TECH,
        ('半导体', '芯片', '电子', '计算机', '软件', '通信', '5G', '人工智能', '游戏',
         '传媒', '网安', '信创', '军工电子', '消费电子', 'TMT', '云计算', '大数据'),
    ),
    (
        ETFCategory.SECTOR_FINANCE,
        ('银行', '证券', '保险', '券商', '金融', '非银'),
    ),
    (
        ETFCategory.SECTOR_CONSUMER,
        ('消费', '食品', '饮料', '酒', '家电', '汽车', '旅游', '零售', '免税', '养殖'),
    ),
    (
        ETFCategory.SECTOR_MEDICAL,
        ('医药', '医疗', '生物', '疫苗', '创新药', '中药', '器械', '健康', '制药'),
    ),
    (
        ETFCategory.SECTOR_INDUSTRIAL,
        ('军工', '国防', '机械', '基建', '建筑', '运输', '机器人', '高端制造', '电力设备',
         '新能源', '锂电', '光伏', '风电', '储能', '核电', '航空'),
    ),
    (
        ETFCategory.SECTOR_ENERGY,
        ('能源', '煤炭', '石油', '油气', '电力'),
    ),
    (
        ETFCategory.SECTOR_MATERIAL,
        ('材料', '化工', '建材', '钢铁', '有色', '稀土', '锂', '金属', '矿业'),
    ),
)


def _text(value):
    return str(value or '').strip()


# TuShare's ``benchmark`` is a free-text index description up to 200 chars; the first
# universe sync aborted on a varchar(50) overflow when a classifier branch returned it
# verbatim as ``sub_category``. Read the bound off the model so the two cannot drift.
SUB_CATEGORY_MAX_LENGTH = ETFUniverse._meta.get_field('sub_category').max_length


def _short(value):
    return _text(value)[:SUB_CATEGORY_MAX_LENGTH]


# ``pro.fund_share`` reports ``fd_share`` in 万份, which is how TuShare's own documentation
# states it and how the live probe came back (515020.SH -> 44084.53). Everything downstream
# compares against 元-denominated turnover, so shares are converted to 份 on ingest.
SHARE_UNIT_TO_SHARES = 10000

TRACK_INDEX_MAX_LENGTH = ETFUniverse._meta.get_field('track_index_key').max_length

# TuShare writes the tracked index as free text, and one index appears in several
# decorations. Measured over the 1882 rankable rows (763 distinct benchmarks): all of them
# use ``×`` and none uses ``*``; exactly one blends two legs with ``+``; 10 carry
# ``(人民币)``. The decorations that repeat are the quote convention and the return clause,
# so ``中证银行指数收益率×100%`` and ``中证银行指数×100%`` have to normalise to one key while
# ``中证800银行指数收益率×100%`` stays separate -- it really is a different index.
_TRACK_INDEX_PARENTHETICAL = re.compile(r'[（(][^（）()]*[）)]')
_TRACK_INDEX_WEIGHT = re.compile(r'[×*]\s*\d+(?:\.\d+)?\s*%')
_TRACK_INDEX_TAIL_TOKENS = ('指数收益率', '收益率', '指数')


def normalize_track_index(name, benchmark=''):
    """Return the cluster key for the index a fund tracks.

    Two ETFs with the same key are the same bet in different wrappers, which is what makes
    this the dedup axis for the rotation pool: ``category`` is too coarse (19 bank ETFs are
    one category, and ``中证银行`` / ``中证800银行`` / ``沪深300非银行金融`` sit inside it) while
    raw ``benchmark`` is too fine (the same index is written 12 different ways in it).

    The key is a normalisation, not a claim about identity: funds whose benchmark is blank
    fall back to their name, so they cluster with nothing but themselves.
    """
    raw = _text(benchmark) or _text(name)
    if not raw:
        return ''
    key = _TRACK_INDEX_PARENTHETICAL.sub('', raw)
    key = _TRACK_INDEX_WEIGHT.sub('', key)
    key = re.sub(r'\s+', '', key)
    key = ''.join(key.split()).lower()
    changed = True
    while changed and len(key) > 1:
        changed = False
        for token in _TRACK_INDEX_TAIL_TOKENS:
            if key.endswith(token) and len(key) > len(token):
                key = key[: -len(token)]
                changed = True
                break
    return key[:TRACK_INDEX_MAX_LENGTH]


def shares_to_aum_yuan(shares_outstanding, close):
    """Estimated fund size in 元, or ``None`` when either input is missing.

    ``shares_outstanding`` is a period stamp, not a live figure: ``fund_share`` is published
    irregularly, so a stale count multiplied by today's price is an estimate and is treated
    as one (it ranks candidates within a cluster, it never sizes a position).
    """
    if shares_outstanding is None or close is None:
        return None
    try:
        shares = float(shares_outstanding)
        price = float(close)
    except (TypeError, ValueError):
        return None
    if shares <= 0 or price <= 0:
        return None
    return shares * price


def is_excluded_code(ts_code):
    """True when the exchange code names a LOF or a 分级 share rather than an ETF.

    The check is prefix-and-suffix, not prefix alone: ``159xxx`` (SZ ETF) and
    ``150xxx`` (SZ 分级 share) share a two-character root, so reading only ``'15'``
    would have removed 768 genuine ETFs from the pool.
    """
    code = _text(ts_code).upper()
    if '.' not in code:
        return False
    digits, _, suffix = code.partition('.')
    return any(digits.startswith(prefix) for prefix in EXCLUDED_CODE_PREFIXES.get(suffix, ()))


def is_excluded_vehicle(ts_code, name, fund_type, invest_type):
    """True for on-exchange funds the rotation pool must never hold.

    The exclusion is deliberately independent of ``status``: a fund that is delisted
    still belongs in the table (see ``sync_etf_universe``), it just must not be a
    vehicle we can rank.
    """
    if fund_type in EXCLUDED_FUND_TYPES:
        return True
    if invest_type in EXCLUDED_INVEST_TYPES:
        return True
    if is_excluded_code(ts_code):
        return True
    label = _text(name)
    return any(token in label for token in EXCLUDED_NAME_TOKENS)


def _matched_category(text, keywords, category):
    return any(token in text for token in keywords)


def classify_etf(name, invest_type='', benchmark='', fund_type=''):
    """Return ``(category, sub_category)`` for one ``fund_basic`` row.

    ``category`` is never blank -- anything the three tiers cannot place becomes
    ``other`` so it is visible for review rather than silently dropped.
    """
    label = f'{_text(name)} {_text(benchmark)}'
    invest = _text(invest_type)

    if invest == '货币型' or (
        _matched_category(label, MONEY_KEYWORDS, None)
        and not _matched_category(label, MONEY_EXCLUDE_KEYWORDS, None)
    ):
        return ETFCategory.MONEY, _short(name)
    if invest == '黄金现货合约' or _matched_category(label, COMMODITY_KEYWORDS, None):
        return ETFCategory.COMMODITY, _short(benchmark) or _short(name)
    if _matched_category(label, BOND_KEYWORDS, None):
        return ETFCategory.BOND, _short(benchmark) or _short(name)
    if _matched_category(label, QDII_KEYWORDS, None):
        return ETFCategory.QDII, _short(benchmark) or _short(name)
    if _matched_category(label, DIVIDEND_KEYWORDS, None):
        return ETFCategory.STYLE_DIVIDEND, _short(benchmark) or _short(name)
    # Sectors are tested before BROAD on purpose. ``中证全指`` and ``沪深300`` are
    # index *families* that sector indices are built inside, so "中证全指半导体" and
    # "沪深300医药" name a sector, not the broad market. Reading BROAD first routed
    # every 中证全指 sector fund to ``broad``.
    for category, keywords in SECTOR_KEYWORDS:
        if _matched_category(label, keywords, category):
            return category, _short(benchmark) or _short(name)
    if _matched_category(label, BROAD_KEYWORDS, None):
        return ETFCategory.BROAD, _short(benchmark) or _short(name)
    if _matched_category(label, GROWTH_KEYWORDS, None):
        return ETFCategory.STYLE_GROWTH, _short(benchmark) or _short(name)
    if invest in INDEX_INVEST_TYPES:
        return ETFCategory.BROAD, _short(benchmark) or _short(name)
    return ETFCategory.OTHER, _short(benchmark) or _short(name)


def is_index_fund(invest_type, benchmark):
    """Whether the row looks like a genuine index-tracking ETF.

    Used for reporting and for the "too new to rank" reasoning, never to exclude a
    fund from storage -- excluding here would recreate the survivorship problem the
    point-in-time pool filter is built to avoid.
    """
    return _text(invest_type) in INDEX_INVEST_TYPES or bool(_text(benchmark))


def eligible_universe(trade_date, min_listing_age_days=183):
    """ETFs that could have been bought on ``trade_date``.

    Point-in-time on purpose: today's ``is_active`` list would score momentum only on
    the funds that happened to survive. ``pit_list_date`` is the SQL ``COALESCE`` of
    ``list_date`` and the ``first_trade_date`` fallback (156 of 2987 rows have no
    ``list_date``), so the missing dates do not narrow the pool for unrelated reasons.
    It is deliberately *not* named ``effective_list_date``, which is a read-only model
    property: an annotation with that alias shadows it on the instance and any later
    ``.filter()`` re-evaluation through the ORM raises ``no setter``.

    The age bound is a *minimum*: a fund joins the pool only once it has been listed for
    ``min_listing_age_days`` calendar days, so ``list_date``/``first_trade_date`` must be
    at or before ``trade_date`` minus that many days. That single comparison also implies
    the fund had listed by ``trade_date``, so no separate "not yet listed" clause is needed.

    ``is_rankable`` is the vehicle-type exclusion, recorded at sync time. It cannot be
    re-derived here because the keyword rules would then drift out of agreement with
    what was stored, and it must not be replaced by ``status``/``is_active``, which
    would reintroduce the survivorship bias this filter exists to avoid.

    The other half of the pool filter -- rolling 20-session turnover above
    ``ETF_MIN_AVG_AMOUNT_YUAN`` -- is not here because a queryset cannot express a
    trailing window over ``ETFDaily``. ``features.build_eligibility_frame`` evaluates it
    per session and records the combined verdict as ``ETFFeatureSnapshot.is_eligible``,
    which is what the backtest and the live signal read.
    """
    return (
        ETFUniverse.objects.annotate(pit_list_date=Coalesce('list_date', 'first_trade_date'))
        .filter(is_rankable=True)
        .filter(
            Q(pit_list_date__isnull=False),
            pit_list_date__lte=_age_floor(trade_date, min_listing_age_days),
        )
        .filter(
            Q(delist_date__isnull=True) | Q(delist_date__gt=trade_date),
        )
    )


def _age_floor(trade_date, min_listing_age_days):
    """Latest ``pit_list_date`` a fund may have to be old enough on ``trade_date``."""
    return trade_date - timedelta(days=int(min_listing_age_days))


def is_point_in_time_eligible(etf, trade_date, min_listing_age_days=183):
    """Row-level mirror of :func:`eligible_universe` for backtests holding objects."""
    if not etf.is_rankable:
        return False
    effective = etf.effective_list_date
    if effective is None:
        return False
    if effective > _age_floor(trade_date, min_listing_age_days):
        return False
    if etf.delist_date is not None and etf.delist_date <= trade_date:
        return False
    return True
