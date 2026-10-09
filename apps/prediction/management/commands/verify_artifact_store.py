"""Verify registered model artifacts resolve in the configured artifact store.

Operationalizes TECHNICAL_GUIDE 6.5 ("path resolution should be checked as part of
any promotion") and the cutover validation step in docs/how-to/artifact-store.md.
It reports whether each registry ``artifact_path`` resolves in the configured
backend and exits non-zero if any *active* artifact is missing -- the worst failure
mode, because an active artifact is selected by version and then fails at inference
time rather than at promotion time.

    python manage.py verify_artifact_store            # human-readable report
    python manage.py verify_artifact_store --json      # machine-readable

Run it after ``migrate_artifacts_to_store`` and before untracking ``models/``, and
as a promotion sanity check. ``LightGBMModelArtifact`` is the LightGBM deployment
authority and ``ModelVersion(LSTM)`` the LSTM authority (TECHNICAL_GUIDE 6.1);
``ModelVersion(ENSEMBLE)`` rows point at a DB-only weights snapshot with no file, so
they are not checked.
"""
import json

from django.core.management.base import BaseCommand, CommandError

from apps.prediction.artifact_store import get_artifact_store, to_store_key


class Command(BaseCommand):
    help = 'Verify registered model artifacts resolve in the configured artifact store.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--json', action='store_true',
            help='Emit a machine-readable JSON report instead of prose.',
        )

    def handle(self, *args, **options):
        from apps.prediction.models import ModelVersion
        from apps.prediction.models_lightgbm import LightGBMModelArtifact

        store = get_artifact_store()
        as_json = bool(options.get('json'))

        reachable, store_error = self._probe(store)
        lightgbm = self._check_lightgbm(LightGBMModelArtifact, store)
        lstm = self._check_lstm(ModelVersion, store)

        all_entries = lightgbm + lstm
        missing_active = [e['label'] for e in all_entries if e['is_active'] and not e['present']]
        report = {
            'backend': type(store).__name__,
            'is_remote': store.is_remote,
            'store_reachable': reachable,
            'store_error': store_error,
            'totals': {
                'lightgbm_rows': len(lightgbm),
                'lightgbm_present': sum(1 for e in lightgbm if e['present']),
                'lstm_rows': len(lstm),
                'lstm_present': sum(1 for e in lstm if e['present']),
                'missing_active': len(missing_active),
            },
            'missing_active': missing_active,
            'lightgbm': lightgbm,
            'lstm': lstm,
        }

        if as_json:
            self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        else:
            self._render(report)

        if reachable is False:
            raise CommandError(f'Artifact store unreachable: {store_error}')
        if missing_active:
            raise CommandError(
                f'{len(missing_active)} ACTIVE artifact(s) do not resolve in the store: '
                + ', '.join(missing_active)
            )
        if not as_json:
            self.stdout.write(self.style.SUCCESS('All active artifacts resolve in the store.'))

    def _probe(self, store):
        """For a remote store, confirm it is reachable. Returns (reachable, error)."""
        if not store.is_remote:
            return None, None
        try:
            store.exists_prefix('models')  # MaxKeys=1 list; raises if unreachable
            return True, None
        except Exception as exc:  # noqa: BLE001 - report, do not crash the report
            return False, str(exc)

    def _check_lightgbm(self, model, store):
        entries = []
        for art in model.objects.all().order_by('horizon_days', 'version'):
            path = art.artifact_path or ''
            entries.append({
                'label': f'lgb-{art.horizon_days}d:{art.version}',
                'version': art.version,
                'horizon_days': art.horizon_days,
                'is_active': bool(art.is_active),
                'artifact_path': path,
                'present': bool(path) and store.exists_prefix(to_store_key(path)),
            })
        return entries

    def _check_lstm(self, model, store):
        entries = []
        qs = model.objects.filter(model_type=model.ModelType.LSTM).order_by('-is_active', 'version')
        for mv in qs:
            path = mv.artifact_path or ''
            entries.append({
                'label': f'lstm:{mv.version}',
                'version': mv.version,
                'is_active': bool(mv.is_active),
                'artifact_path': path,
                'present': bool(path) and store.exists_prefix(to_store_key(path)),
            })
        return entries

    def _render(self, report):
        self.stdout.write(f"Artifact store backend: {report['backend']} (remote={report['is_remote']})")
        if report['is_remote']:
            state = 'reachable' if report['store_reachable'] else f"UNREACHABLE: {report['store_error']}"
            self.stdout.write(f'Store: {state}')
        totals = report['totals']
        self.stdout.write(
            f"LightGBM artifacts: {totals['lightgbm_present']}/{totals['lightgbm_rows']} resolve; "
            f"LSTM versions: {totals['lstm_present']}/{totals['lstm_rows']} resolve"
        )
        missing = [
            entry
            for group in (report['lightgbm'], report['lstm'])
            for entry in group
            if not entry['present']
        ]
        if missing:
            self.stdout.write(self.style.WARNING(f'{len(missing)} registered artifact(s) do not resolve:'))
            for entry in missing[:50]:
                flag = ' [ACTIVE]' if entry['is_active'] else ''
                self.stdout.write(f"  - {entry['label']}{flag}: {entry['artifact_path'] or '(blank)'}")
