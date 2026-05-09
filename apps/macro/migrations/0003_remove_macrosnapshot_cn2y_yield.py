from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('macro', '0002_add_macro_yield_surface_fields'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='macrosnapshot',
            name='cn2y_yield',
        ),
    ]