"""Rewrite the persisted JSON metadata keys that RenameField cannot reach.

`MacroSnapshot.dxy` became `fxcm_usdollar`, but the provenance lives in JSON keyed by the OLD
field name: `metadata.fx_quote_sources`, `metadata.fx_sources`, `metadata.field_sources`,
`metadata.errors`, and error keys of the form `dxy_<provider code>` / `dxy_error`. A
RenameField rewrites the column, not these keys, so without this migration every historical
row would lose the record of which instrument actually produced its value.

Rows written by the akshare fallback only carry `cny_usd` (providers.py:535), so missing keys
are normal and must not be treated as an error. Reversible: the reverse pass maps back.
"""

from django.db import migrations

OLD = "dxy"
NEW = "fxcm_usdollar"
KEYED_MAPS = ("fx_quote_sources", "fx_sources", "field_sources", "errors")


def _rewrite(metadata, source, target):
    if not isinstance(metadata, dict):
        return metadata, False
    changed = False
    for map_name in KEYED_MAPS:
        payload = metadata.get(map_name)
        if isinstance(payload, dict) and source in payload:
            payload[target] = payload.pop(source)
            changed = True
    # Error keys are built as f'{field}_{ts_code}' and f'{field}_error' (providers.py:466,484;
    # backfill_macro_snapshots.py:460,483,524), so they carry the field name as a prefix.
    for key in list(metadata):
        if key == f"{source}_error" or key.startswith(f"{source}_"):
            metadata[target + key[len(source):]] = metadata.pop(key)
            changed = True
    return metadata, changed


def forwards(apps, schema_editor):
    MacroSnapshot = apps.get_model("macro", "MacroSnapshot")
    rewritten = 0
    for snapshot in MacroSnapshot.objects.exclude(metadata={}):
        metadata, changed = _rewrite(snapshot.metadata, OLD, NEW)
        if changed:
            snapshot.metadata = metadata
            snapshot.save(update_fields=["metadata"])
            rewritten += 1
    if rewritten:
        print(f"rewrote dxy metadata keys on {rewritten} MacroSnapshot row(s)", flush=True)


def backwards(apps, schema_editor):
    MacroSnapshot = apps.get_model("macro", "MacroSnapshot")
    for snapshot in MacroSnapshot.objects.exclude(metadata={}):
        metadata, changed = _rewrite(snapshot.metadata, NEW, OLD)
        if changed:
            snapshot.metadata = metadata
            snapshot.save(update_fields=["metadata"])


class Migration(migrations.Migration):

    dependencies = [
        ("macro", "0005_rename_dxy_fxcm_usdollar"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
