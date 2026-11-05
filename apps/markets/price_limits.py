"""Daily price-limit rules for the A-share boards.

A return that exceeds the board's limit cannot be a real trade, so it is the
signature this project has been unable to see: an adjusted-price series whose basis
changed mid-series (see ``docs/reference/price-adjustment.md``).

Two things this module deliberately does not pretend to know:

* The limits below changed by regulation on the dates in the constants. A rule that
  ignores ``trade_date`` would flag every 创业板 bar before 2020-08-24 as anomalous,
  which is a far larger error than the drift it is meant to detect.
* Where the rule is genuinely uncertain -- B-share regimes, ST on the Beijing
  exchange, the delisting-arrangement period -- this returns ``None`` meaning
  "no limit applies". For an audit that is the safe direction: an unknown rule
  skips a bar rather than inventing an anomaly. A caller that would *reject* the
  row must not treat ``None`` as permission to drop data.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

BOARD_MAIN = 'main'
BOARD_CHINEXT = 'chinext'
BOARD_STAR = 'star'
BOARD_BSE = 'bse'

LIMIT_TEN = Decimal('0.10')
LIMIT_TWENTY = Decimal('0.20')
LIMIT_THIRTY = Decimal('0.30')
LIMIT_ST = Decimal('0.05')

#: ChiNext moved from 10% to 20% with the registration-system reform.
CHINEXT_WIDER_LIMIT_FROM = date(2020, 8, 24)
#: STAR opened already at 20%; the board did not exist before this date.
STAR_MARKET_FIRST_TRADING_DAY = date(2019, 7, 22)
#: The Beijing exchange opened with a 30% limit on this date.
BSE_FIRST_TRADING_DAY = date(2021, 11, 15)

#: Sessions after an IPO with no price limit. Under the registration system this is
#: five for every board; before 2023 the main board allowed a wider first day than
#: ±10% but not a narrow one. Five sessions is therefore the conservative common
#: answer for old and new listings alike, and it errs toward skipping.
NEW_LISTING_FREE_SESSIONS = 5

_ST_PREFIXES = ('S*ST', 'SST', '*ST', 'ST')

#: An unrecognised exchange suffix means the symbol prefix cannot be trusted to
#: mean anything, so the board stays unknown rather than guessed.
KNOWN_SUFFIXES = frozenset({'SH', 'SZ', 'BJ'})


def board_for(ts_code):
    """The board a ``ts_code`` belongs to, or ``None`` when it is not recognisable."""
    token = str(ts_code or '').strip().upper()
    if '.' not in token:
        return None
    symbol, suffix = token.split('.', 1)
    if not symbol.isdigit() or suffix not in KNOWN_SUFFIXES:
        return None
    if suffix == 'BJ' or symbol.startswith(('43', '83', '87', '88', '920')):
        return BOARD_BSE
    if symbol.startswith(('300', '301', '302')):
        return BOARD_CHINEXT
    if symbol.startswith(('688', '689')):
        return BOARD_STAR
    if symbol.startswith(('600', '601', '603', '605', '000', '001', '002', '003', '900', '200')):
        return BOARD_MAIN
    return None


def is_st_name(name):
    """Whether an asset name carries a special-treatment prefix.

    The marker is always followed by Chinese characters, so a Latin-script name
    that merely begins with the same two letters must not be narrowed to ±5%.
    Misclassifying one would manufacture an anomaly where there is none.
    """
    text = str(name or '').strip().upper()
    for prefix in _ST_PREFIXES:
        if not text.startswith(prefix):
            continue
        remainder = text[len(prefix):]
        if not remainder:
            return True
        first = remainder[0]
        if not first.isascii() or not first.isalpha():
            return True
    return False


def limit_pct_for(ts_code, trade_date=None, name='', sessions_since_listing=None):
    """Maximum one-session move as a fraction, or ``None`` when no limit applies.

    ``sessions_since_listing`` is the count of earlier stored bars for the same
    asset. The caller scanning a series already knows it, and it is exact for any
    asset whose series starts at its IPO -- which is what the listing-coverage
    check in ``validate_data_quality`` already asserts.
    """
    if sessions_since_listing is not None and sessions_since_listing < NEW_LISTING_FREE_SESSIONS:
        return None

    board = board_for(ts_code)
    if board is None:
        return None

    if board == BOARD_BSE:
        if trade_date is not None and trade_date < BSE_FIRST_TRADING_DAY:
            return None
        return LIMIT_THIRTY

    if board == BOARD_STAR:
        if trade_date is not None and trade_date < STAR_MARKET_FIRST_TRADING_DAY:
            return None
        return LIMIT_TWENTY

    if board == BOARD_CHINEXT:
        # Special treatment never narrowed ChiNext or STAR to 5%; the two boards
        # keep their own limit for ST names.
        if trade_date is None or trade_date >= CHINEXT_WIDER_LIMIT_FROM:
            return LIMIT_TWENTY
        return LIMIT_TEN

    if is_st_name(name):
        return LIMIT_ST
    return LIMIT_TEN
