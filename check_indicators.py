import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.analytics.models import TechnicalIndicator
from django.db.models import Count

summary = TechnicalIndicator.objects.values('indicator_type').annotate(
    count=Count('id')
).order_by('indicator_type')

print('\nIndicator Summary:')
print('=' * 40)
total = 0
for item in summary:
    count = item['count']
    total += count
    print(f"{item['indicator_type']:20} {count:6} records")
print('=' * 40)
print(f"{'TOTAL':20} {total:6} records")
print()
