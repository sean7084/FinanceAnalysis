"""#154: renaming the column must not strand the provenance stored under the old key.

`RenameField` moves the column; it cannot reach `metadata.fx_quote_sources['dxy']`,
`field_sources['dxy']`, `errors['dxy']` or the `dxy_<code>` / `dxy_error` keys. Migration 0006
rewrites them, and these tests pin both directions plus the cases that must be left alone.
"""

import importlib
from decimal import Decimal

from django.apps import apps as django_apps
from django.test import TestCase, override_settings

from apps.macro.models import MacroSnapshot

migration = importlib.import_module('apps.macro.migrations.0006_rewrite_dxy_metadata_keys')


class MetadataKeyRewriteTests(TestCase):
    def setUp(self):
        self.metadata = {
            'fx_quote_sources': {'dxy': 'USDOLLAR.FXCM', 'cny_usd': 'USDCNH'},
            'fx_sources': {'dxy': 'tushare'},
            'field_sources': {'dxy': 'tushare', 'cny_usd': 'tushare'},
            'errors': {'dxy': 'provider timeout'},
            'dxy_USDOLLAR.FXCM': 'quote rejected',
            'dxy_error': 'normalisation failed',
            'fallback_fields': ['dxy', 'cny_usd'],
        }

    def test_forwards_rewrites_every_keyed_namespace(self):
        rewritten, changed = migration._rewrite(self.metadata, 'dxy', 'fxcm_usdollar')
        self.assertTrue(changed)
        self.assertEqual(rewritten['fx_quote_sources']['fxcm_usdollar'], 'USDOLLAR.FXCM')
        self.assertEqual(rewritten['fx_sources']['fxcm_usdollar'], 'tushare')
        self.assertEqual(rewritten['field_sources']['fxcm_usdollar'], 'tushare')
        self.assertEqual(rewritten['errors']['fxcm_usdollar'], 'provider timeout')
        self.assertEqual(rewritten['fxcm_usdollar_USDOLLAR.FXCM'], 'quote rejected')
        self.assertEqual(rewritten['fxcm_usdollar_error'], 'normalisation failed')
        self.assertNotIn('dxy', rewritten['fx_quote_sources'])
        self.assertNotIn('dxy_error', rewritten)

    def test_other_fields_and_list_values_are_untouched(self):
        rewritten, _ = migration._rewrite(self.metadata, 'dxy', 'fxcm_usdollar')
        self.assertEqual(rewritten['fx_quote_sources']['cny_usd'], 'USDCNH')
        self.assertEqual(rewritten['field_sources']['cny_usd'], 'tushare')
        # fallback_fields is a LIST of field names, not a keyed map; the migration deliberately
        # does not rewrite list members, so this pins that boundary rather than assuming it.
        self.assertEqual(rewritten['fallback_fields'], ['dxy', 'cny_usd'])

    def test_reverse_restores_the_old_keys(self):
        forward, _ = migration._rewrite(self.metadata, 'dxy', 'fxcm_usdollar')
        back, changed = migration._rewrite(forward, 'fxcm_usdollar', 'dxy')
        self.assertTrue(changed)
        self.assertEqual(back['fx_quote_sources']['dxy'], 'USDOLLAR.FXCM')
        self.assertEqual(back['dxy_error'], 'normalisation failed')
        self.assertEqual(back['dxy_USDOLLAR.FXCM'], 'quote rejected')

    def test_akshare_rows_without_the_key_are_left_alone(self):
        sparse = {'fx_quote_sources': {'cny_usd': 'akshare'}, 'field_sources': {'cny_usd': 'akshare'}}
        result, changed = migration._rewrite(sparse, 'dxy', 'fxcm_usdollar')
        self.assertFalse(changed)
        self.assertEqual(result, sparse)

    def test_non_dict_metadata_is_tolerated(self):
        for value in (None, [], 'legacy', 0):
            result, changed = migration._rewrite(value, 'dxy', 'fxcm_usdollar')
            self.assertFalse(changed)
            self.assertEqual(result, value)


class RenameSurfaceTests(TestCase):
    def test_model_declares_the_new_name_and_not_the_old(self):
        names = {field.name for field in MacroSnapshot._meta.get_fields()}
        self.assertIn('fxcm_usdollar', names)
        self.assertNotIn('dxy', names)

    def test_help_text_names_the_actual_instrument(self):
        help_text = str(MacroSnapshot._meta.get_field('fxcm_usdollar').help_text)
        self.assertIn('USDOLLAR.FXCM', help_text)
        self.assertIn('NOT the ICE DXY', help_text)

    def test_release_lag_uses_the_renamed_key(self):
        from apps.macro.models import release_lag_days
        with override_settings(MACRO_RELEASE_LAG_DAYS={'fxcm_usdollar': 1}):
            self.assertEqual(release_lag_days('fxcm_usdollar'), 1)

    def test_legacy_key_in_env_falls_back_and_warns(self):
        from apps.macro.apps import MacroConfig
        from apps.macro.models import release_lag_days
        with override_settings(MACRO_RELEASE_LAG_DAYS={'dxy': 1}, MACRO_PUBLICATION_LAG_DAYS_FALLBACK=35):
            # env.json replaces the default dict, so the renamed series is absent and falls back.
            self.assertEqual(release_lag_days('fxcm_usdollar'), 35)
            with self.assertLogs('apps.macro', level='WARNING') as captured:
                MacroConfig('apps.macro', django_apps.get_app_config('macro').module).ready()
            self.assertTrue(any('fxcm_usdollar' in message for message in captured.output))

    def test_no_legacy_key_means_no_warning(self):
        from apps.macro.apps import MacroConfig
        with override_settings(MACRO_RELEASE_LAG_DAYS={'fxcm_usdollar': 1}):
            import logging
            logger = logging.getLogger('apps.macro')
            with self.assertNoLogs(logger, level='WARNING'):
                MacroConfig('apps.macro', django_apps.get_app_config('macro').module).ready()


class MigrationAppliesTests(TestCase):
    def test_existing_row_metadata_is_rewritten_by_the_migration(self):
        snapshot = MacroSnapshot.objects.create(
            date='2026-01-01',
            fxcm_usdollar=Decimal('100.0'),
            metadata={'fx_quote_sources': {'dxy': 'USDOLLAR.FXCM'}, 'dxy_error': 'x'},
        )
        migration.forwards(django_apps, None)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.metadata['fx_quote_sources']['fxcm_usdollar'], 'USDOLLAR.FXCM')
        self.assertEqual(snapshot.metadata['fxcm_usdollar_error'], 'x')
        self.assertNotIn('dxy', snapshot.metadata['fx_quote_sources'])

        migration.backwards(django_apps, None)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.metadata['fx_quote_sources']['dxy'], 'USDOLLAR.FXCM')
        self.assertEqual(snapshot.metadata['dxy_error'], 'x')
