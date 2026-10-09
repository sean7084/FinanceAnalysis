"""Mirror on-disk model artifacts into the configured artifact store.

Model artifacts live under ``models/`` as a local cache. This command uploads them
into the store selected by ``ARTIFACT_STORE_BACKEND``. Under the default ``local``
backend the cache *is* the store, so uploads are no-ops and the command only reports
what is present; under ``s3`` it uploads each family to the bucket.

Idempotent and dry-run by default -- pass ``--execute`` to actually upload::

    python manage.py migrate_artifacts_to_store                     # dry run, report
    python manage.py migrate_artifacts_to_store --execute           # upload to store
    python manage.py migrate_artifacts_to_store --execute --normalize-registry

``--normalize-registry`` rewrites registry ``artifact_path`` values that resolve
under ``BASE_DIR`` to canonical store keys. Legacy absolute paths from *other*
hosts cannot be mapped and are left untouched (reported).
"""
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.prediction.artifact_store import get_artifact_store, to_store_key


class Command(BaseCommand):
    help = 'Mirror local model artifacts (models/) into the configured artifact store.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--execute', action='store_true',
            help='Actually upload; without it the command is a dry run.',
        )
        parser.add_argument(
            '--root', default=None,
            help='Artifact root to walk (default: <BASE_DIR>/models).',
        )
        parser.add_argument(
            '--normalize-registry', action='store_true',
            help='Rewrite registry artifact_path values that resolve under BASE_DIR '
                 'to canonical store keys (requires --execute).',
        )

    def handle(self, *args, **options):
        execute = bool(options['execute'])
        store = get_artifact_store()
        root = options['root'] or os.path.join(str(settings.BASE_DIR), 'models')

        if not os.path.isdir(root):
            self.stdout.write(self.style.WARNING(f'No artifact root at {root}; nothing to do.'))
            return

        mode = 'EXECUTE' if execute else 'DRY RUN'
        self.stdout.write(f'Artifact store backend: {type(store).__name__} ({mode})')
        self.stdout.write(f'Walking {root}')

        families = self._discover_families(root)
        if not families:
            self.stdout.write(self.style.WARNING('No artifact families found.'))
            return

        totals = {'families': 0, 'files': 0, 'bytes': 0, 'uploaded': 0}
        for family_dir in families:
            key_prefix = to_store_key(family_dir)
            files, size = self._measure_dir(family_dir)
            totals['families'] += 1
            totals['files'] += files
            totals['bytes'] += size
            if execute:
                count = store.upload_dir(family_dir, key_prefix)
                totals['uploaded'] += count
                self.stdout.write(f'  uploaded {count} file(s) -> {key_prefix}/ ({size} bytes)')
            else:
                self.stdout.write(f'  would upload {files} file(s) -> {key_prefix}/ ({size} bytes)')

        summary = (
            f"{mode} summary: {totals['families']} families, {totals['files']} files, "
            f"{totals['bytes']} bytes"
        )
        if execute:
            summary += f", {totals['uploaded']} uploaded"
        self.stdout.write(self.style.SUCCESS(summary))

        if options['normalize_registry']:
            if not execute:
                self.stdout.write(self.style.WARNING(
                    '--normalize-registry needs --execute; skipped.'
                ))
            else:
                self._normalize_registry()

    def _discover_families(self, root):
        """Return leaf family dirs (``models/<type>/<family>``) sorted for stable output."""
        families = []
        for type_name in sorted(os.listdir(root)):
            type_dir = os.path.join(root, type_name)
            if not os.path.isdir(type_dir):
                continue
            for family_name in sorted(os.listdir(type_dir)):
                family_dir = os.path.join(type_dir, family_name)
                if os.path.isdir(family_dir):
                    families.append(family_dir)
        return families

    def _measure_dir(self, directory):
        files = 0
        size = 0
        for dirpath, _dirnames, filenames in os.walk(directory):
            for name in filenames:
                files += 1
                try:
                    size += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    pass
        return files, size

    def _normalize_registry(self):
        from apps.prediction.models import ModelVersion
        from apps.prediction.models_lightgbm import LightGBMModelArtifact

        changed = 0
        skipped = 0
        for model in (LightGBMModelArtifact, ModelVersion):
            for row in model.objects.exclude(artifact_path='').iterator():
                current = row.artifact_path or ''
                canonical = to_store_key(current)
                if not canonical or canonical == current:
                    continue
                if os.path.isabs(canonical):
                    # Legacy path from another host: cannot be mapped to a key.
                    skipped += 1
                    continue
                row.artifact_path = canonical
                row.save(update_fields=['artifact_path'])
                changed += 1
        self.stdout.write(self.style.SUCCESS(
            f'Normalized {changed} registry artifact_path value(s); '
            f'{skipped} legacy absolute path(s) left unchanged.'
        ))
