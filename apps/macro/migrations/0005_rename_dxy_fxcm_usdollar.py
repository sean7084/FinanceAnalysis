"""Rename MacroSnapshot.dxy to fxcm_usdollar (#154).

The column holds the Dow Jones FXCM Dollar Index Basket (TuShare USDOLLAR.FXCM / USDOLLAR),
never the ICE DXY. The old name was a lie in a table where every other column is read by its
own name, and it had already propagated into the regime feature name and the DQ field list.

RenameField moves the column; the verbose_name/help_text change is recorded here too so
`makemigrations --check` stays clean. The persisted JSON metadata keys are rewritten by the
following data migration (0006), which RenameField cannot reach.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("macro", "0004_delete_eventimpactstat"),
    ]

    operations = [
        migrations.RenameField(
            model_name="macrosnapshot",
            old_name="dxy",
            new_name="fxcm_usdollar",
        ),
        migrations.AlterField(
            model_name="macrosnapshot",
            name="fxcm_usdollar",
            field=models.DecimalField(
                blank=True,
                decimal_places=4,
                help_text=(
                    "TuShare USDOLLAR.FXCM (legacy code USDOLLAR): the Dow Jones FXCM Dollar "
                    "Index Basket, NOT the ICE DXY. Renamed from dxy in #154 because the old "
                    "name claimed an instrument this column never held. Per-row provenance is "
                    "recorded in metadata.fx_quote_sources['fxcm_usdollar']."
                ),
                max_digits=10,
                null=True,
                verbose_name="Dow Jones FXCM US Dollar Index Basket",
            ),
        ),
    ]
