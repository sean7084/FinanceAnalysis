"""Executable-universe policy (campaign B1, UNI-05 / STK-11, issue #197).

The main account cannot trade ChiNext or STAR (24-month trading-record
anniversary 2027-10-27), so those boards are DISABLED BY DEFAULT for every
universe consumer — serving, training, backtest, and benchmark construction.
The switch is a committed constant, not an env var: flipping it re-baselines
every reading and must be a reviewable change (the v2 path is a dated PR after
the UNI-V2A checkpoint, with the degraded ChiNext-only fallback if the STAR
500k asset threshold is unmet). BSE is classified and disabled defensively;
measured constituents in both indices are zero today, so the exclusion is a
no-op that prevents future silent leakage (owner point O1).

The ST layer is an independent switch (spec: decoupled from the board layer).
It stays disabled until the namechange reconciliation is archived (card B3);
requesting it before then fails closed.

Research presets exist so the comparison can measure the full universe and the
LSY-style cap arm without touching the serving default. Every override is
recorded in run/artifact provenance by the callers (card B1b).
"""
from apps.markets.price_limits import BOARD_BSE, BOARD_CHINEXT, BOARD_STAR, board_for

POLICY_DEFAULT = 'default'
POLICY_RESEARCH_FULL = 'research_full'
POLICY_RESEARCH_CAP = 'research_cap'
VALID_POLICIES = frozenset({POLICY_DEFAULT, POLICY_RESEARCH_FULL, POLICY_RESEARCH_CAP})

#: Legal trading permissions per board. False = excluded from the universe.
BOARD_TRADING_PERMISSIONS = {
    BOARD_CHINEXT: False,
    BOARD_STAR: False,
    BOARD_BSE: False,
}

#: Independent ST-layer switch; flipped only by card B3 after archived reconciliation.
ST_LAYER_ENABLED = False

#: LSY-style "exclude smallest 30% by circulating market cap" arm fraction
#: (pre-registered trigger; the harness supplies the per-date masks, card H2).
CAP_EXCLUDE_FRACTION = 0.30


class UniversePolicyError(ValueError):
    """Raised for unknown policies or fail-closed layer requests."""


def resolve_board_permissions(policy):
    if policy == POLICY_RESEARCH_FULL:
        return {board: True for board in BOARD_TRADING_PERMISSIONS}
    return dict(BOARD_TRADING_PERMISSIONS)


def resolve_st_layer(policy, st_layer):
    """Effective ST exclusion: explicit per-run override, else the global switch
    for the default policy and off for research presets. Requesting ST while the
    global switch is disabled fails closed (reconciliation gate, card B3)."""
    if st_layer is None:
        return ST_LAYER_ENABLED if policy == POLICY_DEFAULT else False
    if st_layer and not ST_LAYER_ENABLED:
        raise UniversePolicyError(
            'ST layer requested but ST_LAYER_ENABLED is False; archive the '
            'namechange reconciliation first (card B3).'
        )
    return bool(st_layer)


def apply_universe_policy(
    asset_ids,
    trade_date,
    *,
    policy=POLICY_DEFAULT,
    board_permissions=None,
    st_layer=None,
    cap_exclude=False,
    asset_ts_codes=None,
    st_asset_ids=None,
    cap_asset_ids=None,
):
    """Filter one date's asset ids through the board / ST / cap layers.

    Caches are caller-built so batch paths load once: ``asset_ts_codes`` maps
    id -> ts_code (queried from Asset when absent), ``st_asset_ids`` is the set
    ST as-of ``trade_date``, ``cap_asset_ids`` the smallest-30% cap set. The
    filter never reads listing_status / delist_date: delisted non-ST names stay
    in the historical universe (survivorship discipline, spec §4.2).
    """
    if policy not in VALID_POLICIES:
        raise UniversePolicyError(f'unknown universe policy {policy!r}; valid: {sorted(VALID_POLICIES)}')
    permissions = resolve_board_permissions(policy) if board_permissions is None else board_permissions
    st_on = resolve_st_layer(policy, st_layer)
    ids = list(asset_ids)
    if not ids:
        return []

    if asset_ts_codes is None:
        from apps.markets.models import Asset

        asset_ts_codes = dict(Asset.objects.filter(id__in=ids).values_list('id', 'ts_code'))
    excluded_boards = {board for board, allowed in permissions.items() if not allowed}
    if excluded_boards:
        ids = [i for i in ids if board_for(asset_ts_codes.get(i, '')) not in excluded_boards]

    if st_on:
        if st_asset_ids is None:
            raise UniversePolicyError(
                'ST layer enabled but no ST interval data provided; run '
                'sync_asset_namechanges and wire st_history (card B2).'
            )
        ids = [i for i in ids if i not in st_asset_ids]

    if cap_exclude or policy == POLICY_RESEARCH_CAP:
        if cap_asset_ids is None:
            raise UniversePolicyError(
                'cap exclusion requested but no per-date cap mask provided '
                '(the walk-forward harness supplies circ_mv masks, card H2).'
            )
        ids = [i for i in ids if i not in cap_asset_ids]

    return ids