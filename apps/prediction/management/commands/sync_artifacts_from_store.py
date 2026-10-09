"""Populate the local artifact cache from the configured store.

Once the store is the source of truth, ``models/`` is untracked, so a fresh clone
has no artifacts on disk. This command downloads them from the store into the local
cache so inference and training load locally (no network round-trip per read). Under
the default ``local`` backend the cache *is* the store, so this is a no-op.

    python manage.py sync_artifacts_from_store                    # sync all under models/
    python manage.py sync_artifacts_from_store --prefix models/lightgbm
"""
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.prediction.artifact_store import get_artifact_store


class Command(BaseCommand):
    help = 'Download model artifacts from the store into the local cache (for fresh clones).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--prefix', default='models',
            help='Store key prefix to sync (default: models).',
        )

    def handle(self, *args, **options):
        store = get_artifact_store()
        prefix = (options['prefix'] or 'models').strip('/')
        cache_root = str(getattr(settings, 'ARTIFACT_LOCAL_CACHE_ROOT', None) or settings.BASE_DIR)
        local_dir = os.path.join(cache_root, prefix.replace('/', os.sep))

        if not store.is_remote:
            self.stdout.write(self.style.WARNING(
                f'Backend {type(store).__name__} is local; the cache is already the store. '
                'Nothing to sync.'
            ))
            return

        keys = store.list_keys(prefix)
        self.stdout.write(f'Store holds {len(keys)} object(s) under {prefix}/')
        downloaded = store.ensure_dir_local(prefix, local_dir)
        self.stdout.write(self.style.SUCCESS(
            f'Downloaded {downloaded} missing object(s) into {local_dir} '
            f'({max(len(keys) - downloaded, 0)} already cached).'
        ))
