"""HTTP views for the shared ``apps.core`` infrastructure app.

Currently only exposes the SPA fallback that renders the Vite-built frontend
shell. Kept in ``apps.core`` (rather than ``config.urls``) so the project-level
URLConf stays focused on API routers, matching the existing convention that
cross-app shared helpers live here.
"""

from django.views.generic import TemplateView


class SPAFallbackView(TemplateView):
    """Render the Vite SPA shell for any non-API, non-admin path.

    django-vite resolves the correct asset URLs based on ``DJANGO_VITE_DEV_MODE``:

    * ``True`` -- emits absolute ``<script>`` URLs against the Vite dev server
      (``http://localhost:5173/@vite/client``, ``.../src/main.tsx``), so HMR
      keeps working even though the HTML itself came from Django on ``:8000``.
    * ``False`` -- reads ``frontend/dist/.vite/manifest.json`` and emits the
      hashed ``/static/`` URLs produced by ``npm run build``. WhiteNoise serves
      those straight from ``STATIC_ROOT`` after ``collectstatic``.

    Client-side routing (react-router-dom) handles every deep link under the
    catch-all pattern in ``config/urls.py``, so this view never needs to know
    about individual SPA routes.
    """

    template_name = "frontend/index.html"


spa_fallback = SPAFallbackView.as_view()
