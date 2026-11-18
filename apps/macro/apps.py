import logging

from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)


class MacroConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.macro'

    def ready(self):
        # env.json REPLACES the default dict rather than merging into it, and release_lag_days()
        # falls back to a conservative 35 days for any field it does not find. So a deployment
        # whose .env still carries the pre-#154 'dxy' key gets a silently over-long lag for the
        # renamed series -- safe against look-ahead, but wrong, and invisible without this.
        configured = getattr(settings, 'MACRO_RELEASE_LAG_DAYS', {}) or {}
        if isinstance(configured, dict) and 'dxy' in configured:
            logger.warning(
                "MACRO_RELEASE_LAG_DAYS still carries the legacy 'dxy' key. The field was "
                "renamed to 'fxcm_usdollar' (#154), and env.json replaces the default dict "
                "instead of merging, so that series is using the conservative %s-day fallback "
                "lag. Rename the key in .env to restore its intended publication lag.",
                getattr(settings, 'MACRO_PUBLICATION_LAG_DAYS_FALLBACK', 35),
            )
