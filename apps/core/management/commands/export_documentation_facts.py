"""Regenerate the ``docs/reference/`` fact sheets from live repository state.

The project keeps two kinds of documentation:

* **authored prose** -- explanations, rationale, workflows. Hand-written and
  reviewed like code.
* **facts** -- row counts, coverage ranges, active model artifacts, command
  inventories, Celery routes, environment variables. These already exist in
  the database and in ``config/``; transcribing them by hand is what made
  earlier documentation drift.

This command owns the second kind. It introspects the ORM, the management
command registry, the Celery app, the settings AST, and the on-disk model
artifact metadata, then writes five Markdown files under ``docs/reference/``.

    python manage.py export_documentation_facts
    python manage.py export_documentation_facts --only celery,env
    python manage.py export_documentation_facts --check

``--check`` regenerates into memory and fails with a non-zero exit code when
any committed file differs, which makes documentation drift a test failure
rather than a review accident. Generated output is deterministic apart from
the timestamp line, which ``--check`` ignores.
"""

import argparse
import ast
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management import get_commands, load_command_class
from django.core.management.base import BaseCommand, CommandError
from django.db import models
from django.db.models import Count, Max, Min
from django.db.utils import OperationalError, ProgrammingError

SHEET_NAMES = ('metrics', 'models', 'commands', 'celery', 'env')

# Ordered preference for the column that anchors a table's coverage range.
DATE_FIELD_PRIORITY = (
    'date',
    'timestamp',
    'trade_date',
    'snapshot_date',
    'as_of',
    'published_at',
    'cal_date',
    'created_at',
    'start_date',
)

# Dimensions worth breaking a row count down by, keyed "<app_label>.<Model>".
BREAKDOWN_FIELDS = {
    'analytics.TechnicalIndicator': 'indicator_type',
    'analytics.SignalEvent': 'signal_type',
    'sentiment.SentimentScore': 'score_type',
    'prediction.ModelVersion': 'model_type',
    'backtest.BacktestRun': 'status',
    'markets.Asset': 'listing_status',
    'markets.IndexMembership': 'index_code',
}

# Tables whose individual columns matter to model quality, so non-null
# coverage is reported per field rather than per table.
FIELD_COVERAGE_MODELS = (
    'factors.FundamentalFactorSnapshot',
    'factors.CapitalFlowSnapshot',
    'factors.FactorScore',
    'macro.MacroSnapshot',
)

# Tables with no operational meaning in a coverage report.
SKIPPED_MODELS = frozenset({
    'developer.ChangelogEntry',
})

NUMERIC_FIELD_TYPES = (models.IntegerField, models.FloatField, models.DecimalField)

# Options Django's BaseCommand adds to every parser. Identical everywhere, so
# they are stated once here rather than repeated for all thirty-odd commands.
DJANGO_BUILTIN_OPTIONS = frozenset({
    '--version', '-v', '--verbosity', '--settings', '--pythonpath',
    '--traceback', '--no-color', '--force-color', '--skip-checks',
})

# Matches a leading ISO date in a string default.
ISO_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}')

# A date default this close to the current day is treated as computed from it and
# rendered symbolically. Fixed historical constants such as the 2010 data floor are
# thousands of days away and stay literal.
DYNAMIC_DATE_WINDOW_DAYS = 400


def _reference_today():
    """Return the date that dynamic defaults are measured against.

    UTC, matching the project's ``TIME_ZONE``/``USE_TZ`` configuration. Commands are
    inconsistent about this -- most use ``date.today()`` (local) while some use
    ``timezone.now().date()`` (UTC) -- so the two references disagree for part of
    every day in any non-UTC timezone. Rendering an exact day offset would therefore
    make this sheet differ between a morning and an evening run and break ``--check``.
    The offset is deliberately not published; see ``_describe_default``.
    """
    return datetime.now(timezone.utc).date()


def _utc_stamp():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _header(title, source_of_truth):
    """Return the standard generated-file preamble for ``title``."""
    return '\n'.join([
        '<!--',
        '  GENERATED FILE - DO NOT EDIT BY HAND.',
        '  Regenerate: python manage.py export_documentation_facts',
        f'  Source of truth: {source_of_truth}.',
        '-->',
        '',
        f'# {title}',
        '',
        f'_Generated: {_utc_stamp()}_',
        '',
    ])


def _md_table(headers, rows):
    """Render ``rows`` as a GitHub-flavoured Markdown table."""
    lines = [
        '| ' + ' | '.join(headers) + ' |',
        '| ' + ' | '.join('---' for _ in headers) + ' |',
    ]
    for row in rows:
        lines.append('| ' + ' | '.join(str(cell) for cell in row) + ' |')
    return '\n'.join(lines)


def _num(value):
    """Format a numeric aggregate without scientific notation or long tails."""
    if value is None:
        return ''
    if isinstance(value, float):
        return f'{value:.6f}'.rstrip('0').rstrip('.')
    return str(value)


def _date(value):
    if value is None:
        return '&mdash;'
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)


def _project_app_labels():
    """Return the app labels that belong to this project, in INSTALLED_APPS order."""
    return [
        entry.split('.')[-1]
        for entry in settings.INSTALLED_APPS
        if entry.startswith('apps.')
    ]


def _resolve_date_field(model):
    """Return the first field on ``model`` that can anchor a coverage range."""
    concrete = {field.name: field for field in model._meta.concrete_fields}
    for candidate in DATE_FIELD_PRIORITY:
        field = concrete.get(candidate)
        if isinstance(field, (models.DateField, models.DateTimeField)):
            return field
    return None


def _resolve_asset_field(model):
    """Return the asset foreign key on ``model``, if it has one."""
    for field in model._meta.concrete_fields:
        if field.name == 'asset' and isinstance(field, models.ForeignKey):
            return field
    return None


def _iter_project_models():
    """Yield ``(app_label, model)`` for every concrete project model."""
    labels = set(_project_app_labels())
    for model in apps.get_models():
        label = model._meta.app_label
        if label not in labels:
            continue
        key = f'{label}.{model.__name__}'
        if key in SKIPPED_MODELS:
            continue
        yield label, model


# --------------------------------------------------------------------------- #
# metrics.md
# --------------------------------------------------------------------------- #

def build_metrics():
    """Report row counts, asset spread, and coverage ranges for every table."""
    parts = [_header(
        'Data Coverage Metrics',
        'row counts and date ranges queried from the configured database',
    )]
    parts.append(
        'Table-level coverage for every concrete model in `apps/`. '
        '`Assets` is the distinct count of the `asset` foreign key where the '
        'table has one. Prose about what each metric *means* and what a gap '
        '*implies* lives in `TECHNICAL_GUIDE.md`; this sheet only states the '
        'measured facts.\n',
    )

    rows = []
    breakdowns = []
    for label, model in sorted(_iter_project_models(), key=lambda item: (item[0], item[1].__name__)):
        table = model._meta.db_table
        date_field = _resolve_date_field(model)
        asset_field = _resolve_asset_field(model)
        try:
            queryset = model._default_manager.all()
            total = queryset.count()
            earliest = latest = None
            if date_field is not None and total:
                span = queryset.aggregate(first=Min(date_field.name), last=Max(date_field.name))
                earliest, latest = span['first'], span['last']
            asset_count = ''
            if asset_field is not None and total:
                asset_count = f'{queryset.values(asset_field.name).distinct().count():,}'
        except (OperationalError, ProgrammingError) as exc:
            rows.append((f'`{table}`', f'{label}.{model.__name__}', 'UNAVAILABLE', '', '', '', str(exc)[:80]))
            continue

        rows.append((
            f'`{table}`',
            f'`{label}.{model.__name__}`',
            f'{total:,}',
            asset_count or '&mdash;',
            date_field.name if date_field else '&mdash;',
            _date(earliest),
            _date(latest),
        ))

        breakdown_field = BREAKDOWN_FIELDS.get(f'{label}.{model.__name__}')
        if breakdown_field and total:
            breakdowns.append((model, breakdown_field, date_field, total))

    parts.append('## Table coverage\n')
    parts.append(_md_table(
        ['Table', 'Model', 'Rows', 'Assets', 'Date column', 'Earliest', 'Latest'],
        rows,
    ))

    if breakdowns:
        parts.append('\n## Breakdowns\n')
        for model, breakdown_field, date_field, total in breakdowns:
            parts.append(f'\n### `{model._meta.db_table}` by `{breakdown_field}`\n')
            values = (
                model._default_manager
                .values(breakdown_field)
                .annotate(rows=Count('id'))
                .order_by(breakdown_field)
            )
            sub_rows = []
            for entry in values:
                bucket = entry[breakdown_field]
                filtered = model._default_manager.filter(**{breakdown_field: bucket})
                if date_field is not None:
                    span = filtered.aggregate(first=Min(date_field.name), last=Max(date_field.name))
                    earliest, latest = span['first'], span['last']
                else:
                    earliest = latest = None
                sub_rows.append((
                    f'`{bucket}`',
                    f"{entry['rows']:,}",
                    f"{entry['rows'] / total:.1%}" if total else '',
                    _date(earliest),
                    _date(latest),
                ))
            parts.append(_md_table([breakdown_field, 'Rows', 'Share', 'Earliest', 'Latest'], sub_rows))

    field_rows = []
    for key in FIELD_COVERAGE_MODELS:
        try:
            model = apps.get_model(key)
        except LookupError:
            continue
        date_field = _resolve_date_field(model)
        numeric_fields = [
            field for field in model._meta.concrete_fields
            if isinstance(field, NUMERIC_FIELD_TYPES) and not isinstance(field, models.AutoField)
            and field.name != 'id'
        ]
        if not numeric_fields:
            continue
        aggregates = {}
        for field in numeric_fields:
            aggregates[f'{field.name}__nonnull'] = Count(field.name, filter=models.Q(**{f'{field.name}__isnull': False}))
            aggregates[f'{field.name}__min'] = Min(field.name)
            aggregates[f'{field.name}__max'] = Max(field.name)
        try:
            result = model._default_manager.all().aggregate(**aggregates)
        except (OperationalError, ProgrammingError):
            continue
        for field in numeric_fields:
            nonnull = result[f'{field.name}__nonnull']
            span = ''
            if date_field is not None and nonnull:
                span_query = model._default_manager.filter(**{f'{field.name}__isnull': False})
                bounds = span_query.aggregate(first=Min(date_field.name), last=Max(date_field.name))
                span = f'{_date(bounds["first"])} &rarr; {_date(bounds["last"])}'
            field_rows.append((
                f'`{model._meta.db_table}`',
                f'`{field.name}`',
                f'{nonnull:,}',
                _num(result[f'{field.name}__min']),
                _num(result[f'{field.name}__max']),
                span or '&mdash;',
            ))

    if field_rows:
        parts.append('\n## Field-level non-null coverage\n')
        parts.append(
            'Restricted to the tables whose individual columns feed model '
            'features. A low non-null count with a short span usually means an '
            'upstream provider floor or blackout rather than a bug; see '
            '`docs/how-to/runbook-provider-blackout.md`.\n',
        )
        parts.append(_md_table(
            ['Table', 'Field', 'Non-null rows', 'Min', 'Max', 'Non-null range'],
            field_rows,
        ))

    parts.append('')
    return '\n'.join(parts)


# --------------------------------------------------------------------------- #
# models.md
# --------------------------------------------------------------------------- #

def _scan_disk_artifacts():
    """Return ``(lightgbm_rows, lstm_rows)`` read from on-disk artifact metadata."""
    root = Path(settings.BASE_DIR) / 'models'
    lightgbm_rows = []
    for metadata_path in sorted((root / 'lightgbm').glob('*/metadata.json')):
        try:
            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        pruning = metadata.get('pruning') or {}
        lightgbm_rows.append({
            'dir': metadata_path.parent.name,
            'version': metadata.get('version', ''),
            'horizon': metadata.get('horizon_days', ''),
            'trained_at': (metadata.get('trained_at') or '')[:19].replace('T', ' '),
            'window': f"{metadata.get('training_window_start', '')} \u2192 {metadata.get('training_window_end', '')}",
            'features': len(metadata.get('feature_names') or []),
            'pruning': pruning.get('rule', '&mdash;'),
            'missing': metadata.get('missing_value_strategy', 'ABSENT'),
        })

    lstm_rows = []
    for summary_path in sorted((root / 'lstm').glob('*/summary.json')):
        try:
            summary = json.loads(summary_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        first_artifact = ''
        results = summary.get('results') or {}
        for horizon_result in results.values():
            first_artifact = horizon_result.get('artifact_path') or ''
            if first_artifact:
                break
        lstm_rows.append({
            'dir': summary_path.parent.name,
            'version': summary.get('version', ''),
            'window': f"{summary.get('training_window_start', '')} \u2192 {summary.get('training_window_end', '')}",
            'aggregate_accuracy': _num(summary.get('aggregate_accuracy')),
            'feature_count': next(
                (value.get('feature_count') for value in results.values() if value.get('feature_count')),
                '',
            ),
            'missing': summary.get('missing_value_strategy', 'ABSENT'),
            'artifact_path': first_artifact,
            'resolves': 'yes' if first_artifact and Path(first_artifact).exists() else 'NO',
        })
    return lightgbm_rows, lstm_rows


def build_models():
    """Report the model registry, active artifacts, and on-disk artifact state."""
    parts = [_header(
        'Model Registry and Artifacts',
        'the prediction registry tables plus models/*/metadata.json on disk',
    )]
    parts.append(
        'Read this sheet before trusting any accuracy or feature count quoted '
        'in prose. `LightGBMModelArtifact` is the authority for per-horizon '
        'LightGBM deployment; `ModelVersion` is a higher-level registry that '
        'does **not** keep one simultaneously active row per horizon. The '
        'rationale for that split, and the version-tag naming convention, are '
        'explained in `TECHNICAL_GUIDE.md`; the promote/rollback procedure is '
        'in `docs/how-to/retrain.md`.\n',
    )

    try:
        from apps.prediction.models import ModelVersion
        from apps.prediction.models_lightgbm import (
            EnsembleWeightSnapshot,
            FeatureImportanceSnapshot,
            LightGBMModelArtifact,
        )
    except ImportError as exc:
        parts.append(f'Registry models unavailable: `{exc}`\n')
        return '\n'.join(parts)

    db_available = True
    try:
        active_artifacts = list(
            LightGBMModelArtifact.objects.filter(is_active=True).order_by('horizon_days')
        )
    except (OperationalError, ProgrammingError) as exc:
        db_available = False
        parts.append(f'Database unavailable: `{exc}`\n')

    if db_available:
        parts.append('## Active LightGBM artifacts\n')
        if not active_artifacts:
            parts.append('_No artifact is marked active. LightGBM inference and backtests will fail._\n')
        else:
            rows = []
            for artifact in active_artifacts:
                metrics = artifact.metrics_json or {}
                metadata = artifact.metadata or {}
                pruning = metadata.get('pruning') or {}
                feature_count = len(artifact.feature_names or []) or len(metadata.get('feature_names') or [])
                samples = metrics.get('training_samples') or metadata.get('training_samples')
                stored_path = artifact.artifact_path or ''
                rows.append((
                    artifact.horizon_days,
                    f'`{artifact.version}`',
                    artifact.status,
                    str(artifact.trained_at)[:19].replace('T', ' ') if artifact.trained_at else '',
                    f'{artifact.training_window_start or ""} \u2192 {artifact.training_window_end or ""}',
                    f'{samples:,}' if samples else '&mdash;',
                    _num(metrics.get('accuracy')),
                    feature_count or '&mdash;',
                    pruning.get('rule', '&mdash;'),
                    metadata.get('missing_value_strategy', 'ABSENT'),
                    'yes' if stored_path and Path(stored_path).exists() else 'NO',
                ))
            parts.append(_md_table(
                [
                    'Horizon', 'Version', 'Status', 'Trained at', 'Window', 'Samples',
                    'Accuracy', 'Features', 'Pruning rule', 'Missing-value strategy', 'Path resolves here?',
                ],
                rows,
            ))

        total_artifacts = LightGBMModelArtifact.objects.count()
        parts.append(
            f'\nRegistry totals: **{total_artifacts}** `LightGBMModelArtifact` rows, '
            f'**{len(active_artifacts)}** active.\n',
        )

        parts.append('\n## ModelVersion registry\n')
        version_rows = []
        for version in ModelVersion.objects.order_by('-is_active', 'model_type', '-id'):
            metrics = version.metrics or {}
            accuracy = metrics.get('accuracy') or metrics.get('aggregate_accuracy')
            version_rows.append((
                version.id,
                version.model_type,
                f'`{version.version}`',
                'ACTIVE' if version.is_active else '',
                version.status,
                str(version.trained_at)[:19].replace('T', ' ') if version.trained_at else '',
                _num(accuracy) if accuracy is not None else '&mdash;',
                version.artifact_path or '&mdash;',
            ))
        parts.append(_md_table(
            ['ID', 'Type', 'Version', 'Active', 'Status', 'Trained at', 'Accuracy', 'Artifact path'],
            version_rows,
        ))

        parts.append('\n## Ensemble and diagnostics\n')
        ensemble_rows = []
        for snapshot in EnsembleWeightSnapshot.objects.order_by('-date')[:5]:
            basis = snapshot.basis_metrics or {}
            ensemble_rows.append((
                snapshot.id,
                snapshot.date,
                _num(snapshot.lightgbm_weight),
                _num(snapshot.lstm_weight),
                _num(snapshot.heuristic_weight),
                snapshot.basis_lookback_days,
                _num(basis.get('lightgbm_accuracy')),
                _num(basis.get('lstm_accuracy')),
                _num(basis.get('heuristic_accuracy')),
            ))
        if ensemble_rows:
            parts.append(_md_table(
                [
                    'ID', 'Date', 'LightGBM weight', 'LSTM weight', 'Heuristic weight',
                    'Lookback (d)', 'basis: LightGBM acc', 'basis: LSTM acc', 'basis: heuristic acc',
                ],
                ensemble_rows,
            ))
        importance_count = FeatureImportanceSnapshot.objects.count()
        latest_importance = FeatureImportanceSnapshot.objects.order_by('-id').first()
        parts.append(
            f'\n`FeatureImportanceSnapshot`: **{importance_count:,}** rows'
            + (f', latest id `{latest_importance.id}`.' if latest_importance else '.')
            + '\n',
        )

    lightgbm_disk, lstm_disk = _scan_disk_artifacts()
    parts.append('\n## On-disk LightGBM artifacts\n')
    if lightgbm_disk:
        parts.append(_md_table(
            ['Directory', 'Version', 'Horizon', 'Trained at', 'Window', 'Features', 'Pruning rule', 'Missing-value strategy'],
            [
                (
                    f'`{row["dir"]}`', row['version'], row['horizon'], row['trained_at'],
                    row['window'], row['features'], row['pruning'], row['missing'],
                )
                for row in lightgbm_disk
            ],
        ))
    else:
        parts.append('_No `models/lightgbm/*/metadata.json` found._\n')

    parts.append('\n## On-disk LSTM artifacts\n')
    if lstm_disk:
        parts.append(_md_table(
            ['Directory', 'Version', 'Window', 'Aggregate accuracy', 'Features', 'Missing-value strategy', 'Stored path resolves here?'],
            [
                (
                    f'`{row["dir"]}`', row['version'], row['window'], row['aggregate_accuracy'],
                    row['feature_count'], row['missing'], row['resolves'],
                )
                for row in lstm_disk
            ],
        ))
        unresolvable = [row for row in lstm_disk if row['resolves'] == 'NO']
        if unresolvable:
            parts.append(
                '\n> **Portability warning.** Stored `artifact_path` values are '
                'absolute and were written on the host that trained them. '
                f'{len(unresolvable)} of {len(lstm_disk)} do not resolve on this '
                'machine. Retraining rewrites them; see '
                '`docs/how-to/retrain.md`.\n',
            )
    else:
        parts.append('_No `models/lstm/*/summary.json` found._\n')

    if db_available and active_artifacts:
        disk_versions = {row['version'] for row in lightgbm_disk}
        db_versions = {artifact.version for artifact in active_artifacts}
        missing_on_disk = sorted(db_versions - disk_versions)
        if missing_on_disk:
            parts.append(
                '\n> **Mismatch.** Active in the database but absent from '
                f'`models/lightgbm/`: {", ".join(f"`{v}`" for v in missing_on_disk)}.\n',
            )

    parts.append('')
    return '\n'.join(parts)


# --------------------------------------------------------------------------- #
# commands.md
# --------------------------------------------------------------------------- #

def _symbolic_offset(target):
    """Return a stable token if ``target`` is computed from the current day.

    Only the *fact* that a default is date-relative is published, never the exact
    offset. An offset such as ``<today-1d>`` looks more informative but is not
    reproducible: local-date and UTC-date defaults diverge for part of every day,
    so the same command renders differently depending on when the sheet is built.
    Collapsing to one token keeps ``--check`` a meaningful gate.

    Returns ``None`` for dates far from today, which are fixed constants and are
    rendered literally.
    """
    delta = abs((target - _reference_today()).days)
    if delta > DYNAMIC_DATE_WINDOW_DAYS:
        return None
    return '`<dynamic-date>`'


def _describe_default(action):
    """Render an argparse default as stable Markdown.

    Date defaults are usually computed from today, which would make the
    generated sheet differ on every run and break ``--check``. They are
    normalised to a symbolic offset from today instead, which is both
    deterministic and more informative than a literal date. Commands express
    these either as ``date`` objects or as ISO strings, so both are handled.
    """
    default = action.default
    if default in (None, argparse.SUPPRESS):
        return '&mdash;'
    value = default
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return _symbolic_offset(value) or f'`{value.isoformat()}`'
    if isinstance(default, str):
        match = ISO_DATE_RE.match(default)
        if match:
            try:
                parsed = date.fromisoformat(match.group(0))
            except ValueError:
                parsed = None
            if parsed is not None:
                symbolic = _symbolic_offset(parsed)
                if symbolic:
                    return symbolic
    if isinstance(default, bool):
        return '`True`' if default else '`False`'
    if isinstance(default, (list, tuple, set)):
        return f'`{",".join(map(str, sorted(default))) or "[]"}`'
    if default == '':
        return '`""`'
    return f'`{default}`'


def build_commands():
    """Report every project management command with its full option surface."""
    parts = [_header(
        'Management Command Reference',
        'django.core.management.get_commands() and each command\'s argument parser',
    )]
    parts.append(
        'Workflow ordering and the reasoning behind each stage live in '
        '`docs/how-to/backfill.md` and `docs/how-to/retrain.md`. This sheet is '
        'the option surface only.\n\n'
        'Every command also accepts the standard Django options '
        '(`--version`, `-v/--verbosity`, `--settings`, `--pythonpath`, '
        '`--traceback`, `--no-color`, `--force-color`, `--skip-checks`); they '
        'are omitted from the per-command tables below.\n\n'
        '`<dynamic-date>` marks a default computed from the current day rather '
        'than a fixed constant. The exact offset is not published because '
        'commands differ in whether they derive it from the local date '
        '(`date.today()`) or from UTC (`timezone.now().date()`), and those '
        'disagree for part of every day outside UTC. Read the `Help` column for '
        'the intended semantics, or the command source for the precise '
        'expression.\n',
    )

    registry = get_commands()
    grouped = {}
    for name, app_label in registry.items():
        if not app_label.startswith('apps.'):
            continue
        grouped.setdefault(app_label.split('.')[-1], []).append(name)

    total = sum(len(names) for names in grouped.values())
    parts.append(f'**{total}** project commands across **{len(grouped)}** apps.\n')

    for app_label in sorted(grouped):
        parts.append(f'\n## `apps.{app_label}`\n')
        for name in sorted(grouped[app_label]):
            try:
                # load_command_class() returns an instantiated Command, not the class.
                instance = load_command_class(f'apps.{app_label}', name)
                parser = instance.create_parser('manage.py', name)
            except Exception as exc:  # noqa: BLE001 - report, never abort the sheet
                parts.append(f'\n### `{name}`\n\n_Parser unavailable: `{exc}`_\n')
                continue

            help_text = (instance.help or '').strip()
            parts.append(f'\n### `{name}`\n')
            if help_text:
                parts.append(f'{help_text}\n')

            option_rows = []
            for action in parser._actions:
                if isinstance(action, argparse._HelpAction):
                    continue
                if not action.option_strings:
                    continue
                if any(option in DJANGO_BUILTIN_OPTIONS for option in action.option_strings):
                    continue
                kind = 'flag' if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)) else 'value'
                required = 'yes' if action.required else ''
                option_rows.append((
                    ', '.join(f'`{option}`' for option in action.option_strings),
                    kind,
                    _describe_default(action),
                    required,
                    (action.help or '').strip(),
                ))
            if option_rows:
                parts.append(_md_table(['Option', 'Takes', 'Default', 'Required', 'Help'], option_rows))
            else:
                parts.append('_No options._')
            parts.append('')

    return '\n'.join(parts)


# --------------------------------------------------------------------------- #
# celery.md
# --------------------------------------------------------------------------- #

def _describe_crontab(schedule):
    """Render a celery crontab as a readable five-field expression."""
    fields = []
    for attribute in ('_orig_minute', '_orig_hour', '_orig_day_of_week', '_orig_day_of_month', '_orig_month_of_year'):
        value = getattr(schedule, attribute, None)
        if value is None:
            fields.append('*')
        elif isinstance(value, (set, frozenset, list, tuple)):
            fields.append(','.join(str(item) for item in sorted(value, key=str)))
        else:
            fields.append(str(value))
    return '`' + ' '.join(fields) + '`'


def build_celery():
    """Report the task inventory, queue topology, routing, and time limits."""
    parts = [_header(
        'Celery Task and Queue Reference',
        'the Celery app registry, CELERY_TASK_ROUTES, and CELERY_BEAT_SCHEDULE',
    )]
    parts.append(
        'Operational guidance -- which worker to start, how to scale backtests, '
        'what to do when a task times out -- lives in '
        '`docs/how-to/local-setup.md` and `docs/how-to/runbook-sync-failure.md`.\n',
    )

    default_queue = getattr(settings, 'CELERY_TASK_DEFAULT_QUEUE', 'celery')
    routes = getattr(settings, 'CELERY_TASK_ROUTES', {}) or {}
    queues = [queue.name for queue in getattr(settings, 'CELERY_TASK_QUEUES', ())]
    global_soft = getattr(settings, 'CELERY_TASK_SOFT_TIME_LIMIT', None)
    global_hard = getattr(settings, 'CELERY_TASK_TIME_LIMIT', None)

    parts.append('## Queue topology\n')
    parts.append(_md_table(
        ['Queue', 'Role'],
        [(f'`{name}`', _queue_role(name, default_queue)) for name in queues] or [
            (f'`{default_queue}`', 'implicit default; no explicit queue declared'),
        ],
    ))
    parts.append(f'\nDefault queue: `{default_queue}`.\n')

    parts.append('\n## Time limits\n')
    parts.append(_md_table(
        ['Scope', 'Soft limit (s)', 'Hard limit (s)'],
        [('Global default', global_soft, global_hard)],
    ))
    parts.append(
        '\nA task decorated with its own `soft_time_limit` / `time_limit` '
        'overrides the global pair. Overrides are listed per task below and '
        'are the usual explanation for a task that ran far longer than '
        f'{global_soft}s without being killed.\n',
    )

    try:
        from config.celery import app as celery_app
        try:
            celery_app.loader.import_default_modules()
        except Exception:  # noqa: BLE001 - best-effort discovery
            pass
        task_names = sorted(name for name in celery_app.tasks if name.startswith('apps.'))
    except Exception as exc:  # noqa: BLE001
        parts.append(f'\nTask registry unavailable: `{exc}`\n')
        task_names = []

    beat = getattr(settings, 'CELERY_BEAT_SCHEDULE', {}) or {}
    scheduled = {}
    for entry_name, entry in beat.items():
        scheduled.setdefault(entry.get('task'), []).append((entry_name, entry))

    parts.append('\n## Beat schedule\n')
    if beat:
        beat_rows = []
        for entry_name in sorted(beat):
            entry = beat[entry_name]
            schedule = entry.get('schedule')
            beat_rows.append((
                f'`{entry_name}`',
                f'`{entry.get("task", "")}`',
                _describe_crontab(schedule) if schedule is not None else str(entry.get('crontab', '&mdash;')),
                ', '.join(f'`{key}`' for key in sorted(entry.get('options') or {})) or '&mdash;',
            ))
        parts.append(_md_table(['Entry', 'Task', 'Crontab (min hour dow dom moy)', 'Options'], beat_rows))
    else:
        parts.append('_No periodic tasks configured._')

    scheduled_task_names = {entry.get('task') for entry in beat.values()}
    unscheduled_count = len(set(task_names) - scheduled_task_names)
    parts.append(
        f'\n**{len(task_names)}** registered project tasks, **{len(beat)}** beat entries, '
        f'**{unscheduled_count}** reachable only through management commands, '
        'API actions, or other tasks.\n',
    )

    parts.append('\n## Task inventory\n')
    rows = []
    for name in task_names:
        task = celery_app.tasks[name]
        route = routes.get(name) or {}
        queue = route.get('queue') or default_queue
        routed = 'explicit' if route else 'default'
        soft = getattr(task, 'soft_time_limit', None)
        hard = getattr(task, 'time_limit', None)
        soft_text = f'**{soft}**' if soft not in (None, global_soft) else (soft if soft is not None else 'global')
        hard_text = f'**{hard}**' if hard not in (None, global_hard) else (hard if hard is not None else 'global')
        beat_entries = [entry_name for entry_name, _ in scheduled.get(name, [])]
        rows.append((
            f'`{name}`',
            f'`{queue}`',
            routed,
            soft_text,
            hard_text,
            ', '.join(f'`{entry}`' for entry in beat_entries) or '&mdash;',
        ))
    parts.append(_md_table(
        ['Task', 'Queue', 'Routing', 'Soft limit', 'Hard limit', 'Beat entries'],
        rows,
    ))

    overrides = [row for row in rows if '**' in str(row[3]) or '**' in str(row[4])]
    if overrides:
        parts.append('\n### Tasks overriding the global time limits\n')
        parts.append(_md_table(['Task', 'Soft limit', 'Hard limit'], [(row[0], row[3], row[4]) for row in overrides]))
        parts.append(
            '\nThese overrides exist because the global soft limit is short '
            'enough to protect the `ops` queue from a stuck sync, while '
            'backtests and retrains legitimately run for tens of minutes.\n',
        )

    parts.append('')
    return '\n'.join(parts)


def _queue_role(name, default_queue):
    roles = {
        'ops': 'periodic syncs, indicators, sentiment, daily predictions (default)',
        'backtest': '`run_backtest`; long-running and CPU-heavy',
        'train-lightgbm': '`train_lightgbm_models`',
        'train-lstm': '`train_lstm_models`',
    }
    return roles.get(name, 'default queue' if name == default_queue else 'declared, unrouted')


# --------------------------------------------------------------------------- #
# env.md
# --------------------------------------------------------------------------- #

def _iter_env_reads():
    """Yield every ``env(...)`` read in the settings package, parsed from the AST."""
    settings_dir = Path(settings.BASE_DIR) / 'config' / 'settings'
    for path in sorted(settings_dir.glob('*.py')):
        if path.name == '__init__.py':
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == 'env':
                kind = 'str'
            elif (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == 'env'
            ):
                kind = func.attr
            else:
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            if not isinstance(node.args[0].value, str):
                continue
            # django-environ's signature is env(name, default=None, **overrides),
            # so a second positional argument is also a default. A keyword wins.
            default = None
            if len(node.args) > 1:
                try:
                    default = ast.unparse(node.args[1])
                except Exception:  # noqa: BLE001
                    default = '<computed>'
            for keyword in node.keywords:
                if keyword.arg == 'default':
                    try:
                        default = ast.unparse(keyword.value)
                    except Exception:  # noqa: BLE001
                        default = '<computed>'
            yield {
                'name': node.args[0].value,
                'kind': kind,
                'default': default,
                'file': path.name,
                'line': node.lineno,
            }


def _parse_env_example():
    """Return the keys declared by the repository's ``.env.example``."""
    path = Path(settings.BASE_DIR) / '.env.example'
    keys = []
    if not path.exists():
        return keys
    for line in path.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in stripped:
            continue
        keys.append(stripped.split('=', 1)[0].strip())
    return keys


def _scan_script_consumers(keys):
    """Map each key to the launcher files that reference it, if any.

    Covers the native launchers under ``scripts/`` and the Compose entrypoints
    under ``compose/``. The Compose entrypoints are extensionless shell scripts
    (``start-celeryworker``), and a key consumed only there -- ``CELERY_WORKER_QUEUES``
    for example -- would otherwise be reported as inert even though a container reads
    it on every boot.
    """
    root = Path(settings.BASE_DIR)
    candidates = []

    scripts_dir = root / 'scripts'
    if scripts_dir.is_dir():
        for path in sorted(scripts_dir.iterdir()):
            if path.suffix in ('.sh', '.ps1') or path.name.startswith('_native_env'):
                candidates.append(path)

    compose_dir = root / 'compose'
    if compose_dir.is_dir():
        for path in sorted(compose_dir.rglob('*')):
            if path.is_file() and path.suffix in ('', '.sh', '.ps1', '.yml', '.yaml'):
                candidates.append(path)

    sources = []
    for path in candidates:
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        try:
            label = path.relative_to(root).as_posix()
        except ValueError:
            label = path.name
        sources.append((label, text))

    consumers = {}
    for key in keys:
        consumers[key] = [label for label, text in sources if key in text]
    return consumers


def build_env():
    """Report every environment variable the settings package reads."""
    parts = [_header(
        'Environment Variable Reference',
        'every env() read in config/settings/*.py, cross-checked against .env.example',
    )]
    parts.append(
        '`.env` at the repository root is the only env file the native stack '
        'needs; `manage.py` sets `DJANGO_READ_DOT_ENV_FILE=True` when it exists. '
        'OS environment variables take precedence over `.env` values.\n',
    )

    reads = {}
    for read in _iter_env_reads():
        reads.setdefault(read['name'], []).append(read)
    example_keys = _parse_env_example()
    example_set = set(example_keys)

    parts.append('## Variables read by settings\n')
    rows = []
    for name in sorted(reads):
        entries = reads[name]
        first = entries[0]
        default = first['default']
        default_text = f'`{default}`' if default is not None else '_required_'
        locations = ', '.join(f'{entry["file"]}:{entry["line"]}' for entry in entries)
        rows.append((
            f'`{name}`',
            f'`{first["kind"]}`',
            default_text,
            'yes' if name in example_set else '**no**',
            locations,
        ))
    parts.append(_md_table(['Variable', 'Type', 'Default', 'In `.env.example`', 'Read at'], rows))

    missing = sorted(name for name in reads if name not in example_set)
    if missing:
        parts.append(
            f'\n**{len(missing)}** variables are env-overridable but absent from '
            f'`.env.example`: {", ".join(f"`{name}`" for name in missing)}. They all '
            'have defaults, so nothing breaks -- but they cannot be discovered '
            'from the example file.\n',
        )

    inert = sorted(key for key in example_keys if key not in reads)
    if inert:
        consumers = _scan_script_consumers(inert)
        parts.append('\n## Keys in `.env.example` that settings never read\n')
        parts.append(
            'These are not Django settings. A key consumed by a launcher under '
            '`scripts/` or a Compose entrypoint under `compose/` still works; a key '
            'with no consumer anywhere has no effect at all and should be wired up or '
            'removed.\n',
        )
        rows = []
        for key in inert:
            found = consumers.get(key) or []
            if found:
                status = 'read by ' + ', '.join(f'`{name}`' for name in found)
            else:
                status = '**no consumer found -- inert**'
            rows.append((f'`{key}`', status))
        parts.append(_md_table(['Key', 'Status'], rows))

    parts.append('')
    return '\n'.join(parts)


BUILDERS = {
    'metrics': (build_metrics, 'Database required.'),
    'models': (build_models, 'Database required for registry sections.'),
    'commands': (build_commands, 'No database access.'),
    'celery': (build_celery, 'No database access.'),
    'env': (build_env, 'No database access.'),
}


class Command(BaseCommand):
    help = (
        'Regenerate the docs/reference/ fact sheets (metrics, models, commands, '
        'celery, env) from live repository and database state.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--output-dir',
            default='docs/reference',
            help='Directory to write the generated sheets into. Defaults to docs/reference.',
        )
        parser.add_argument(
            '--only',
            default='',
            help=f'Comma-separated subset of: {",".join(SHEET_NAMES)}.',
        )
        parser.add_argument(
            '--check',
            action='store_true',
            help='Exit non-zero if any committed sheet differs from freshly generated output.',
        )

    def handle(self, *args, **options):
        selected = self._resolve_selection(options['only'])
        output_dir = Path(options['output_dir'])
        if not output_dir.is_absolute():
            output_dir = Path(settings.BASE_DIR) / output_dir

        drifted = []
        for name in selected:
            builder, requirement = BUILDERS[name]
            self.stdout.write(f'Building {name}.md ({requirement})')
            try:
                content = builder()
            except (OperationalError, ProgrammingError) as exc:
                raise CommandError(f'{name}.md needs the database and it is unavailable: {exc}') from exc

            target = output_dir / f'{name}.md'
            if options['check']:
                if not target.exists():
                    drifted.append(f'{name}.md (missing)')
                elif self._normalize(target.read_text(encoding='utf-8')) != self._normalize(content):
                    drifted.append(name + '.md')
                continue

            output_dir.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding='utf-8', newline='\n')
            self.stdout.write(self.style.SUCCESS(f'  wrote {target}'))

        if options['check']:
            if drifted:
                raise CommandError(
                    'Documentation drift detected: '
                    + ', '.join(drifted)
                    + '. Run `python manage.py export_documentation_facts` and commit the result.'
                )
            self.stdout.write(self.style.SUCCESS('All generated reference sheets are current.'))

    def _resolve_selection(self, only):
        if not only.strip():
            return list(SHEET_NAMES)
        requested = [token.strip() for token in only.split(',') if token.strip()]
        unknown = [token for token in requested if token not in SHEET_NAMES]
        if unknown:
            raise CommandError(
                f'Unknown sheet(s): {", ".join(unknown)}. Valid: {", ".join(SHEET_NAMES)}.'
            )
        return [name for name in SHEET_NAMES if name in requested]

    @staticmethod
    def _normalize(content):
        """Strip the volatile timestamp line so --check compares substance only."""
        return '\n'.join(
            line for line in content.splitlines() if not line.startswith('_Generated:')
        )
