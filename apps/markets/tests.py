from io import StringIO
from unittest.mock import patch

import pandas as pd
from django.core.management import call_command
from django.test import TestCase

from .admin import AssetAdmin
from .models import Asset, Market


class MarketAdminAndBackfillTests(TestCase):
    def setUp(self):
        self.market = Market.objects.create(code='MKT', name='Market Test Exchange')
        self.active_asset = Asset.objects.create(
            market=self.market,
            symbol='600001',
            ts_code='600001.SH',
            name='Active Asset',
        )
        self.delisted_asset = Asset.objects.create(
            market=self.market,
            symbol='600002',
            ts_code='600002.SH',
            name='Delisted Asset',
            listing_status=Asset.ListingStatus.ACTIVE,
        )

    def test_asset_admin_exposes_list_date(self):
        self.assertIn('list_date', AssetAdmin.list_display)
        self.assertIn('list_date', AssetAdmin.list_filter)

    @patch('apps.markets.management.commands.backfill_asset_list_dates.ts.pro_api')
    def test_backfill_asset_list_dates_populates_existing_assets(self, mock_pro_api):
        class StubPro:
            def stock_basic(self, **kwargs):
                list_status = kwargs['list_status']
                if list_status == 'L':
                    return pd.DataFrame([
                        {'ts_code': '600001.SH', 'list_date': '20100105', 'list_status': 'L'},
                    ])
                if list_status == 'D':
                    return pd.DataFrame([
                        {'ts_code': '600002.SH', 'list_date': '20040315', 'list_status': 'D'},
                    ])
                return pd.DataFrame([])

        mock_pro_api.return_value = StubPro()

        output = StringIO()
        with patch('apps.markets.management.commands.backfill_asset_list_dates.settings.TUSHARE_TOKEN', 'test-token'):
            call_command('backfill_asset_list_dates', stdout=output)

        self.active_asset.refresh_from_db()
        self.delisted_asset.refresh_from_db()

        self.assertEqual(self.active_asset.list_date.isoformat(), '2010-01-05')
        self.assertEqual(self.active_asset.listing_status, Asset.ListingStatus.ACTIVE)
        self.assertEqual(self.delisted_asset.list_date.isoformat(), '2004-03-15')
        self.assertEqual(self.delisted_asset.listing_status, Asset.ListingStatus.DELISTED)
        self.assertIn('processed=2', output.getvalue())
        self.assertIn('updated=2', output.getvalue())