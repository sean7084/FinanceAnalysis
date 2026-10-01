"""Custom django-vite app client that keeps the Vite dev server at its root.

django-vite's default ``get_dev_server_url()`` concatenates ``STATIC_URL`` onto
the asset path, producing dev URLs like ``http://localhost:5173/static/src/main.tsx``.
The only way to make the Vite dev server answer those URLs was ``base: '/static/'``
in ``vite.config.ts`` -- which moved the *entire* dev server (and therefore the
pure-Vite HMR flow at ``:5173``) under ``/static/``. ``createBrowserRouter`` in
``frontend/src/App.tsx`` has no basename, so every route 404ed inside its error
boundary when the app was opened at ``http://<host>:5173/static/``.

Production never needed that base: ``get_production_server_url()`` resolves
manifest paths through ``staticfiles_storage.url()``, which prepends
``STATIC_URL`` itself. Only the dev URL scheme needed help, and the help belongs
here rather than in Vite's base.

Vite serves dev modules, the HMR client and the React Refresh preamble at the
origin root, so emit root-relative URLs against the dev server origin.
"""

from urllib.parse import urljoin

from django_vite.core.asset_loader import DjangoViteAppClient


class RootOriginDevClient(DjangoViteAppClient):
    """Dev-mode asset URLs without the ``STATIC_URL`` prefix."""

    def get_dev_server_url(self, path: str) -> str:
        origin = (
            f"{self.dev_server_protocol}://"
            f"{self.dev_server_host}:{self.dev_server_port}/"
        )
        return urljoin(origin, path.lstrip('/'))
