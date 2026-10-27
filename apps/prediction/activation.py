"""Sane bounds on activation for the shared model registry (#5).

A financial direction model that reports 95% accuracy is not a good model; it is a leaked
feature, an off-by-one label or a scoring bug. The registry is shared by stock and ETF arms, so
the bound is enforced once, in ``ModelVersion.save``: an implausible metric cannot carry
``is_active=True`` no matter which code path set it.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError

#: Direction accuracy above this is not credible for a daily equity/fund signal.
MAX_PLAUSIBLE_ACCURACY = 0.75
#: A rank IC above this on this universe is the same kind of artefact.
MAX_PLAUSIBLE_IC = 0.30

ACCURACY_KEYS = ('accuracy', 'direction_accuracy', 'val_accuracy', 'test_accuracy', 'hit_rate')
IC_KEYS = ('ic', 'mean_ic', 'rank_ic', 'ic_mean')


def activation_violations(metrics: dict) -> list[str]:
    violations = []
    for key in ACCURACY_KEYS:
        value = (metrics or {}).get(key)
        if isinstance(value, (int, float)) and value > MAX_PLAUSIBLE_ACCURACY:
            violations.append(
                f'{key}={float(value):.4f} > plausible maximum {MAX_PLAUSIBLE_ACCURACY:.2f}'
            )
    for key in IC_KEYS:
        value = (metrics or {}).get(key)
        if isinstance(value, (int, float)) and value > MAX_PLAUSIBLE_IC:
            violations.append(
                f'{key}={float(value):.4f} > plausible maximum {MAX_PLAUSIBLE_IC:.2f}'
            )
    return violations


def validate_activation_metrics(metrics: dict) -> None:
    """Refuse activation, naming the bound and the reason rather than only saying no."""
    violations = activation_violations(metrics)
    if not violations:
        return
    raise ValidationError(
        'refusing is_active=True: ' + '; '.join(violations) + '. '
        'A metric this high is a leakage or measurement bug, not an edge. '
        f'Bounds: accuracy <= {MAX_PLAUSIBLE_ACCURACY:.2f}, IC <= {MAX_PLAUSIBLE_IC:.2f}.'
    )
