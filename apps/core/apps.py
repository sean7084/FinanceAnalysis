from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.core'

    def ready(self):
        # Django does not import an app's ``checks`` module on its own -- the register
        # decorator only runs when the module is imported. ``core.E001`` guards the
        # TIME_ZONE/session-zone agreement that a TechnicalIndicator write key depends on,
        # so it must actually load in ``manage.py check`` and in CI.
        from apps.core import checks  # noqa: F401
