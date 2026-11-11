"""The dashboard row the frontend declares must be the row the backend declares.

Issue #120: ``DashboardStockRowDto`` in ``frontend/src/lib/api.ts`` carried
``pe_percentile_score`` while ``DashboardStockRowSerializer`` declares
``pe_ttm_percentile_score`` — the name the PE-TTM migration moved to. Nothing rendered blank,
because no component read either name, and every gate stayed green for three reasons this test
exists to close:

* ``tsc`` cannot see interface-vs-payload drift in either direction. An interface field the API
  omits is legal; an API key the interface lacks is legal. The types describe a contract, not
  the other side of it.
* ``vitest`` does not type-check (``.github/workflows/ci.yml`` says so explicitly), so the two
  fixtures that asserted the stale key proved nothing.
* A response-key assertion would not have caught it either — see
  ``DashboardRowAssemblyConformanceTests`` in ``apps/analytics/tests.py`` for the whitelist
  reason, and for the check of what the view actually assembles.

Parsing TypeScript rather than importing a generated schema is deliberate: the endpoint is a
bare ``ViewSet`` with no ``serializer_class``, so ``drf-spectacular`` emits nothing useful about
the row shape, and inventing ``@extend_schema`` annotations to feed a type check would be new
machinery whose only current consumer is this test.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

from apps.analytics.serializers import DashboardStockRowSerializer

FRONTEND_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
INTERFACE = "DashboardStockRowDto"

# A silent regex miss must not read as conformance. The row is 47 fields wide; anything under
# 40 means the parse found a block that is not the interface, so the comparison below would be
# comparing an empty or partial set against a full one.
MIN_PARSED_FIELDS = 40


class DashboardRowShapeTests(SimpleTestCase):
    def _interface_fields(self):
        source = (FRONTEND_SRC / "lib" / "api.ts").read_text(encoding="utf-8")
        block = re.search(rf"export interface {INTERFACE} \{{(.*?)\n\}}", source, re.S)
        if block is None:
            self.fail(
                f"{INTERFACE} not found in frontend/src/lib/api.ts. This gate is only worth "
                "anything while it can read the interface, so it fails rather than passing."
            )

        fields = set()
        for line in block.group(1).splitlines():
            match = re.match(r"([a-z0-9_]+)\??:\s*.+,?$", line.strip())
            if match:
                fields.add(match.group(1))

        self.assertGreaterEqual(
            len(fields), MIN_PARSED_FIELDS,
            f"parsed only {len(fields)} fields from {INTERFACE}; the gate has lost coverage",
        )
        return fields

    def test_interface_and_serializer_declare_the_same_fields(self):
        declared = set(DashboardStockRowSerializer().fields)
        interface = self._interface_fields()

        self.assertEqual(
            sorted(interface - declared), [],
            "the frontend declares fields the serializer never emits — these render as "
            "undefined on every row",
        )
        self.assertEqual(
            sorted(declared - interface), [],
            "the serializer emits fields missing from the frontend interface — a component "
            "cannot read them without an unchecked cast",
        )

    def test_the_optional_candidate_pair_is_the_only_optional_field_pair(self):
        """Rank fields are the only ones the candidate path adds, so optionality is auditable.

        If a third optional field appears, the assembly test in apps/analytics/tests.py has to
        learn about it too; pinning the set here makes that a deliberate edit rather than a
        silent widening of the whitelist.
        """
        source = (FRONTEND_SRC / "lib" / "api.ts").read_text(encoding="utf-8")
        block = re.search(rf"export interface {INTERFACE} \{{(.*?)\n\}}", source, re.S)
        optional = {
            match.group(1)
            for match in re.finditer(r"([a-z0-9_]+)\?:", block.group(1))
        }
        self.assertEqual(optional, {"candidate_rank", "candidate_rank_value"})
