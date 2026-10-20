from django.db import models
from django.utils.translation import gettext_lazy as _


class ChangelogEntry(models.Model):
    """
    API version changelog.  Records breaking changes, new features, fixes, etc.
    Returned by the public ``/api/v1/developer/changelog/`` endpoint.
    """

    class ChangeType(models.TextChoices):
        ADDED = 'ADDED', _('Added')
        CHANGED = 'CHANGED', _('Changed')
        DEPRECATED = 'DEPRECATED', _('Deprecated')
        REMOVED = 'REMOVED', _('Removed')
        FIXED = 'FIXED', _('Fixed')
        SECURITY = 'SECURITY', _('Security')

    version = models.CharField(
        _('API Version'),
        max_length=20,
        help_text=_('Semantic version string, e.g. "1.1.0".'),
    )
    release_date = models.DateField(_('Release Date'))
    change_type = models.CharField(
        _('Change Type'),
        max_length=20,
        choices=ChangeType.choices,
        default=ChangeType.ADDED,
    )
    title = models.CharField(_('Title'), max_length=200)
    description = models.TextField(_('Description'), blank=True)
    is_breaking = models.BooleanField(
        _('Breaking Change'),
        default=False,
        help_text=_('Flag if this change is not backward-compatible.'),
    )
    endpoint = models.CharField(
        _('Affected Endpoint'),
        max_length=200,
        blank=True,
        help_text=_('e.g. "POST /api/v1/backtest/"'),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Changelog Entry')
        verbose_name_plural = _('Changelog Entries')
        ordering = ['-release_date', '-created_at']
        indexes = [
            models.Index(fields=['version']),
            models.Index(fields=['release_date']),
        ]

    def __str__(self):
        breaking = ' [BREAKING]' if self.is_breaking else ''
        return f'[{self.version}] {self.change_type} — {self.title}{breaking}'
