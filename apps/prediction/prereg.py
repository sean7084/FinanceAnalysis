"""Pre-registration manifest parsing and cell gating for the stock harness (H2 / D11).

The preregistration markdown carries a fenced ```json manifest declaring the arms,
panel starts, fold plan, horizons, panel_end, and schema. The harness refuses any
run cell not declared in the manifest (ETF sensitivity precedent). The manifest is
parsed from the FULL file text whose sha256[:12] digest goes into every ledger row,
so the gating and the hash tie to the same document.
"""
import json
import re

from apps.etf_rotation.evidence import preregistration_digest

_MANIFEST_RE = re.compile(r'```json\s*\n(.*?)\n```', re.DOTALL)


class PreregistrationError(ValueError):
    """Raised when a prereg is missing, malformed, or a cell is undeclared."""


def parse_manifest(text: str) -> dict:
    match = _MANIFEST_RE.search(text or '')
    if not match:
        raise PreregistrationError('preregistration has no fenced ```json manifest block')
    try:
        manifest = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise PreregistrationError(f'preregistration manifest is not valid JSON: {exc}') from exc
    for key in ('arms', 'panel_starts', 'panel_end', 'horizons', 'fold_plan', 'schema_version'):
        if key not in manifest:
            raise PreregistrationError(f'preregistration manifest missing required key: {key}')
    return manifest


def validate_cell(manifest: dict, *, arm: str, panel_start: str, horizon: int) -> None:
    """Refuse a run cell not declared in the manifest (no post-hoc starts/arms)."""
    if arm not in manifest['arms'] and arm not in manifest.get('conditional_arms', []):
        raise PreregistrationError(f'arm {arm!r} is not declared in the preregistration')
    if panel_start not in manifest['panel_starts']:
        raise PreregistrationError(f'panel_start {panel_start!r} is not declared in the preregistration')
    if int(horizon) not in manifest['horizons']:
        raise PreregistrationError(f'horizon {horizon!r} is not declared in the preregistration')


def load_preregistration(path) -> tuple[dict, str]:
    """Read a prereg file, return (manifest, digest). Missing file raises."""
    try:
        with open(path, encoding='utf-8') as handle:
            text = handle.read()
    except OSError as exc:
        raise PreregistrationError(f'cannot read preregistration {path}: {exc}') from exc
    return parse_manifest(text), preregistration_digest(text)