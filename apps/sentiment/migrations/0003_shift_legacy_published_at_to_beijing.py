from django.db import migrations

from ._tz_shift_logic import forward, reverse


class Migration(migrations.Migration):
    dependencies = [
        ('sentiment', '0002_rename_synthetic_news_urls'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=reverse, elidable=True),
    ]
