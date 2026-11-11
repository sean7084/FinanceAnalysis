"""Generated sheets must not describe a schema the code no longer has.

``metrics.md`` is the sheet a reader trusts when asking "what does the database
actually hold", and ``--check`` deliberately skips it (``DB_GATED_SHEETS``: its
row counts cannot be reproduced without a populated database). That skip is the
hole this file closes. The decommission dropped three sentiment tables, a
``score_type`` breakdown and two ``factors_factorscore`` columns, and the sheet
kept listing all six because nothing outside a live database was comparing it
to the model set.

The checks are derived rather than enumerated: the table set comes from the
generator's own ``_iter_project_models()``, which walks ``apps.get_models()``.
So a table whose app has left ``INSTALLED_APPS`` is absent from the reference
set without this file naming any app, and the test keeps working through the
next removal instead of needing the next removal's edit.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from apps.core.management.commands.export_documentation_facts import _iter_project_models

METRICS_PATH = Path(settings.BASE_DIR) / 'docs' / 'reference' / 'metrics.md'

# Two adjacent backticked cells: `table` | `app.Model` in the inventory, and
# `table` | `field` in the field-coverage section. The dotted second cell makes
# the two shapes disjoint -- `markets.OHLCV` cannot match the lower-case-only
# field pattern, and a breakdown's `ARTICLE` bucket row has only one cell.
INVENTORY_ROW = re.compile(r"^\| `([a-z][a-z0-9_]*)` \| `([a-z_]+\.[A-Za-z][A-Za-z0-9]*)` \| ")
FIELD_ROW = re.compile(r"^\| `([a-z][a-z0-9_]*)` \| `([a-z][a-z0-9_]*)` \| ")
BREAKDOWN_HEADING = re.compile(r"^### `([a-z][a-z0-9_]*)` by `([a-z][a-z0-9_]*)`")

# A parse that finds almost nothing is a parse that found nothing, and an empty
# reference set would make every assertion below pass. These floors are well
# below the real counts (~50 tables, ~40 field rows) and exist only to fail if
# the sheet's shape changes out from under the regexes.
MIN_INVENTORY_ROWS = 20
MIN_FIELD_ROWS = 10
MIN_BREAKDOWN_HEADINGS = 2


def _reference():
    """``{db_table: {field name, ...}}`` for every concrete project model."""
    reference = {}
    for _label, model in _iter_project_models():
        reference[model._meta.db_table] = {
            field.name for field in model._meta.concrete_fields
        }
    return reference


def _parse(text):
    inventory, fields, headings = [], [], []
    for line in text.splitlines():
        match = INVENTORY_ROW.match(line)
        if match:
            inventory.append(match.groups())
            continue
        match = FIELD_ROW.match(line)
        if match:
            fields.append(match.groups())
            continue
        match = BREAKDOWN_HEADING.match(line)
        if match:
            headings.append(match.groups())
    return inventory, fields, headings


class MetricsSheetModelConformanceTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.text = METRICS_PATH.read_text(encoding='utf-8')
        cls.inventory, cls.fields, cls.headings = _parse(cls.text)
        cls.reference = _reference()

    def test_the_sheet_parses_as_a_sheet(self):
        """Zero coverage is a failure, not a pass."""
        self.assertGreaterEqual(
            len(self.inventory), MIN_INVENTORY_ROWS,
            f'only {len(self.inventory)} inventory rows parsed from {METRICS_PATH.name}; '
            'the table row shape changed and the conformance checks below are vacuous',
        )
        self.assertGreaterEqual(
            len(self.fields), MIN_FIELD_ROWS,
            f'only {len(self.fields)} field-coverage rows parsed; the checks would be vacuous',
        )
        self.assertGreaterEqual(
            len(self.headings), MIN_BREAKDOWN_HEADINGS,
            f'only {len(self.headings)} breakdown headings parsed; the check would be vacuous',
        )

    def test_every_listed_table_is_a_model_the_code_still_has(self):
        unknown = sorted({f'{table} ({label})' for table, label in self.inventory
                          if table not in self.reference})
        self.assertEqual(
            unknown, [],
            f'{METRICS_PATH.name} lists tables no installed app defines. They were dropped, '
            'but --check skips this sheet without a database, so nothing noticed: ' + ', '.join(unknown),
        )

    def test_every_listed_app_label_is_installed(self):
        """The `app.Model` column, checked on its own rather than by the table join.

        Kept separate from the table check because the two fail differently: a table can
        survive an app's removal if another app defines the same db_table, and then only
        the label here would notice.
        """
        installed = set()
        for label, _model in _iter_project_models():
            installed.add(label)
        unknown = sorted({label.split('.')[0] for _table, label in self.inventory
                          if label.split('.')[0] not in installed})
        self.assertEqual(
            unknown, [],
            'inventory rows name an app outside INSTALLED_APPS: ' + ', '.join(unknown),
        )

    def test_every_field_row_names_a_column_its_model_still_declares(self):
        stale = []
        for table, field in self.fields:
            columns = self.reference.get(table)
            if columns is None:
                continue  # already reported by the table check
            if field not in columns:
                stale.append(f'{table}.{field}')
        self.assertEqual(
            stale, [],
            f'{METRICS_PATH.name} reports non-null coverage for columns that no longer exist. '
            'Reading that row as evidence would be wrong even about the numbers next to it: '
            + ', '.join(sorted(set(stale))),
        )

    def test_every_breakdown_slices_a_column_its_model_still_declares(self):
        stale = []
        for table, field in self.headings:
            columns = self.reference.get(table)
            if columns is None:
                continue
            if field not in columns:
                stale.append(f'{table} by {field}')
        self.assertEqual(
            stale, [],
            'breakdown headings name a field the model does not declare: ' + ', '.join(stale),
        )
