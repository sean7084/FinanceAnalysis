"""Rename the persisted lightgbm_inference_backend value windows_gpu -> cuda (#150).

The owner chose a hard rename with no alias, so the serializer now rejects windows_gpu.
BacktestRun.parameters is persisted JSON and rerun_backtests_for_comparison deep-copies it,
so an unmigrated row would carry a value the API refuses. Reversible.

Gitignored local report artifacts (reports/*/matrix_manifest.json and their CSVs) are NOT
rewritten: they are historical outputs, and the docs state that pre-rename artifacts carry the
legacy string and are not directly replayable.
"""

from django.db import migrations

OLD = "windows_gpu"
NEW = "cuda"
KEY = "lightgbm_inference_backend"


def _rewrite(value, source, target):
    if isinstance(value, dict):
        return {k: _rewrite(v, source, target) for k, v in value.items()}
    if isinstance(value, list):
        return [_rewrite(v, source, target) for v in value]
    if value == source:
        return target
    return value


def forwards(apps, schema_editor):
    BacktestRun = apps.get_model("backtest", "BacktestRun")
    changed = 0
    for run in BacktestRun.objects.exclude(parameters={}):
        params = run.parameters or {}
        if params.get(KEY) != OLD:
            continue
        run.parameters = _rewrite(params, OLD, NEW)
        run.save(update_fields=["parameters"])
        changed += 1
    if changed:
        print(f"renamed {OLD} -> {NEW} on {changed} BacktestRun row(s)", flush=True)


def backwards(apps, schema_editor):
    BacktestRun = apps.get_model("backtest", "BacktestRun")
    for run in BacktestRun.objects.exclude(parameters={}):
        params = run.parameters or {}
        if params.get(KEY) != NEW:
            continue
        run.parameters = _rewrite(params, NEW, OLD)
        run.save(update_fields=["parameters"])


class Migration(migrations.Migration):

    dependencies = [
        ("backtest", "0004_remove_backtestrun_user"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
