from django.core.management.base import BaseCommand

from apps.prediction.models import ModelVersion


class Command(BaseCommand):
    help = 'Purge inactive legacy phase14_training_stub LightGBM/LSTM ModelVersion rows.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Delete the matching rows. Defaults to dry-run mode.',
        )

    @staticmethod
    def _stub_queryset():
        return ModelVersion.objects.filter(
            model_type__in=[ModelVersion.ModelType.LIGHTGBM, ModelVersion.ModelType.LSTM],
            is_active=False,
            metadata__source='phase14_training_stub',
        ).order_by('model_type', 'version', 'created_at')

    def handle(self, *args, **options):
        queryset = self._stub_queryset()
        rows = list(queryset.values_list('id', 'model_type', 'version'))
        total = len(rows)

        if total == 0:
            self.stdout.write(self.style.SUCCESS('No inactive legacy prediction stub rows found.'))
            return

        counts_by_type = {}
        for _, model_type, _ in rows:
            counts_by_type[model_type] = counts_by_type.get(model_type, 0) + 1

        breakdown = ', '.join(
            f'{model_type}={counts_by_type[model_type]}'
            for model_type in sorted(counts_by_type)
        )

        if not options['apply']:
            self.stdout.write(
                self.style.WARNING(
                    f'Dry run: would delete {total} inactive legacy prediction stub rows ({breakdown}). '
                    'Re-run with --apply to delete them.'
                )
            )
            return

        deleted_count, _ = queryset.delete()
        self.stdout.write(
            self.style.SUCCESS(
                f'Deleted {deleted_count} inactive legacy prediction stub rows ({breakdown}).'
            )
        )