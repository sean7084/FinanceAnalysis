"""Regular-package marker for the ``apps`` container.

This file must exist. Without it ``apps`` is a PEP 420 implicit namespace
package, and Django's app-label test discovery imports test modules without a
resolved parent package. Every ``apps/*/tests.py`` uses relative imports
(``from .models import ...``), so discovery by app label fails with::

    ImportError: attempted relative import with no known parent package

For example ``manage.py test apps.sentiment`` errors, while the fully qualified
``manage.py test apps.sentiment.tests`` works. Keeping this marker makes both
forms behave identically.
"""
