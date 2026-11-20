"""H2 / D11: prereg manifest parsing and undeclared-cell refusal."""
import json

from django.test import SimpleTestCase

from apps.prediction.prereg import (
    PreregistrationError,
    load_preregistration,
    parse_manifest,
    validate_cell,
)

MANIFEST = {
    'arms': ['csi500_board', 'csi500_board_st', 'csi1000_board', 'csi1000_board_st'],
    'conditional_arms': ['csi500_board_cap', 'csi1000_board_cap'],
    'panel_starts': ['2018-01-02', '2019-01-02', '2020-01-02', '2021-01-04', '2022-01-04'],
    'panel_end': '2026-11-19',
    'horizons': [3, 7, 30],
    'fold_plan': {'type': 'expanding', 'n_folds': 7},
    'schema_version': 'v3',
}
TEXT = '# Prereg\n\nprose\n\n```json\n' + json.dumps(MANIFEST) + '\n```\n'


class PreregManifestTests(SimpleTestCase):
    def test_parse_manifest(self):
        manifest = parse_manifest(TEXT)
        self.assertEqual(manifest['arms'], MANIFEST['arms'])
        self.assertEqual(manifest['panel_end'], '2026-11-19')

    def test_no_manifest_raises(self):
        with self.assertRaises(PreregistrationError):
            parse_manifest('# no manifest here')

    def test_invalid_json_raises(self):
        with self.assertRaises(PreregistrationError):
            parse_manifest('```json\n{not json}\n```')

    def test_missing_key_raises(self):
        bad = dict(MANIFEST)
        del bad['panel_end']
        with self.assertRaises(PreregistrationError):
            parse_manifest('```json\n' + json.dumps(bad) + '\n```')

    def test_declared_cell_passes(self):
        validate_cell(MANIFEST, arm='csi1000_board_st', panel_start='2018-01-02', horizon=30)

    def test_conditional_arm_passes(self):
        validate_cell(MANIFEST, arm='csi1000_board_cap', panel_start='2018-01-02', horizon=30)

    def test_undeclared_arm_refused(self):
        with self.assertRaises(PreregistrationError):
            validate_cell(MANIFEST, arm='csi300_board', panel_start='2018-01-02', horizon=30)

    def test_undeclared_start_refused(self):
        with self.assertRaises(PreregistrationError):
            validate_cell(MANIFEST, arm='csi500_board', panel_start='2023-01-02', horizon=30)

    def test_undeclared_horizon_refused(self):
        with self.assertRaises(PreregistrationError):
            validate_cell(MANIFEST, arm='csi500_board', panel_start='2018-01-02', horizon=14)

    def test_load_preregistration_digest(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile('w', suffix='.md', delete=False, encoding='utf-8') as fh:
            fh.write(TEXT)
            path = fh.name
        try:
            manifest, digest = load_preregistration(path)
            self.assertEqual(manifest['schema_version'], 'v3')
            self.assertEqual(len(digest), 12)
        finally:
            os.unlink(path)

    def test_load_missing_file_raises(self):
        with self.assertRaises(PreregistrationError):
            load_preregistration('/nonexistent/prereg.md')