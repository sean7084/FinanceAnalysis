"""SQL for collapsing the duplicate SentimentScore upsert keys.

Kept separate from the migration (the same shape as ``_tz_shift_logic.py``) so the
statements can be tested against a table without the unique constraint. They had to be
tested: the first version filtered with ``WHERE (score_type, article_id, asset_id, date)
IN (SELECT ...)`` and deleted nothing at all, because SQL ``IN`` uses plain equality and
NULL never equals NULL -- the same NULL-distinctness that produced the duplicates in the
first place, repeating itself inside the repair. ``PARTITION BY`` groups NULLs together,
so the ranking alone identifies the groups and no ``IN`` filter is needed.
"""

COUNT_DUPLICATE_GROUPS = """
SELECT count(*)
  FROM (SELECT 1
          FROM sentiment_sentimentscore
         GROUP BY score_type, article_id, asset_id, date
        HAVING count(*) > 1) t
"""

# Only safe to collapse while no group holds two different published values.
COUNT_DISAGREEING_GROUPS = """
SELECT count(*)
  FROM (SELECT score_type, article_id, asset_id, date,
               count(DISTINCT sentiment_score) AS variants
          FROM sentiment_sentimentscore
         GROUP BY 1, 2, 3, 4
        HAVING count(*) > 1) t
 WHERE variants > 1
"""

# Newest row per key wins; ties break on id so the choice is deterministic and the same
# rows are kept on every retry.
COLLAPSE = """
WITH ranked AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY score_type, article_id, asset_id, date
               ORDER BY created_at DESC, id DESC
           ) AS position
      FROM {table}
)
DELETE FROM {table}
 WHERE id IN (SELECT id FROM ranked WHERE position > 1)
"""


def collapse(schema_editor, table):
    """Delete every row except the newest in each upsert-key group of ``table``.

    The table is required, not defaulted: a helper that silently counts the production
    table while being pointed at a temp one is how the first version of this repair
    looked like it passed.

    Returns the number of rows removed. Refuses to delete anything if some group's copies
    carry different scores: choosing between two published values is a data decision, not
    a cleanup.
    """
    db = schema_editor.connection
    with db.cursor() as cursor:
        cursor.execute(COUNT_DUPLICATE_GROUPS.replace('sentiment_sentimentscore', table))
        groups = cursor.fetchone()[0]
        if not groups:
            return 0

        cursor.execute(COUNT_DISAGREEING_GROUPS.replace('sentiment_sentimentscore', table))
        disagreeing = cursor.fetchone()[0]
        if disagreeing:
            raise RuntimeError(
                f'{disagreeing} duplicate SentimentScore key groups hold differing '
                f'sentiment_score values; collapsing them would change published data. '
                f'Resolve by hand, then re-run.'
            )

        cursor.execute(COLLAPSE.format(table=table))
        removed = cursor.rowcount

        cursor.execute(COUNT_DUPLICATE_GROUPS.replace('sentiment_sentimentscore', table))
        remaining = cursor.fetchone()[0]
        if remaining:
            raise RuntimeError(f'{remaining} duplicate key groups survived the collapse')
    return removed
