"""One project version, three places that must agree with it.

Before ``pyproject.toml`` existed there were three numbers cross-referencing nothing:
``SPECTACULAR_SETTINGS['VERSION']`` said 0.2.0, the CHANGELOG's newest heading said 0.1.53,
and ``frontend/package.json`` said 0.0.0. The OpenAPI one had been lowered from 1.2.0 to
0.2.0 when the authentication module was removed — a genuinely breaking schema change —
which narrowed the gap without closing it, and nothing connected any of them to anything.

``pyproject.toml`` owns the number now. These tests are the connection: a release that
bumps one without the others fails here instead of producing an API schema that claims a
version no artifact was released under.
"""

import json
import re
import tomllib
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]


def _pyproject_version():
    with open(REPO_ROOT / 'pyproject.toml', 'rb') as handle:
        return tomllib.load(handle)['project']['version']


class SingleSourceVersionTests(SimpleTestCase):
    def test_the_api_schema_reports_the_project_version(self):
        self.assertEqual(settings.SPECTACULAR_SETTINGS['VERSION'], _pyproject_version())

    def test_the_frontend_package_reports_the_project_version(self):
        package = json.loads((REPO_ROOT / 'frontend' / 'package.json').read_text(encoding='utf-8'))
        self.assertEqual(package['version'], _pyproject_version())

    def test_the_newest_changelog_heading_reports_the_project_version(self):
        """The Unreleased section sits above the newest release and carries no number."""
        text = (REPO_ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
        versions = re.findall(r'^### version (\S+)$', text, flags=re.MULTILINE)
        self.assertTrue(versions, 'no released version heading in CHANGELOG.md')
        self.assertEqual(versions[0], _pyproject_version())

    def test_the_version_looks_like_a_release_version(self):
        self.assertRegex(_pyproject_version(), r'^\d+\.\d+\.\d+$')