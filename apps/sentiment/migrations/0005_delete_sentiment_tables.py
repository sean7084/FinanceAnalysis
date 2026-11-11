"""Delete the three sentiment tables.

Order is set by the foreign keys, not by alphabetical convenience:
``SentimentScore.article`` points at ``NewsArticle``, so the child table goes first or
Postgres refuses to drop a table another table still references. ``ConceptHeat`` carries no
foreign keys.

The models are already gone from ``apps/sentiment`` -- the app now holds only ``apps.py``
and this migration directory, which is the minimum that keeps ``migrate`` able to find and
run these operations. Nothing here can be undone with data: re-adding the tables is one
migration, re-adding the 6,708,181 score rows and 176,098 articles is not possible, because
the ``major_news`` entitlement was deliberately never purchased and the free per-stock
source takes no date range and returns ten items per call. The reverse operations below
therefore restore empty structure, which is all "add it back later" can honestly mean. The
pre-deletion contents are in the cold backup recorded in
docs/reference/sentiment-data-corrections.md, whose restore was verified against row counts
before anything here ran.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('markets', '0013_purge_csi300_csia500_universe'),
        ('sentiment', '0004_sentiment_score_upsert_key_uniqueness'),
    ]

    operations = [
        migrations.DeleteModel(name='ConceptHeat'),
        migrations.DeleteModel(name='SentimentScore'),
        migrations.DeleteModel(name='NewsArticle'),
    ]
