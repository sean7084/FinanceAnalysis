from .base import *

# STATIC FILES STORAGE
# ------------------------------------------------------------------------------
# WhiteNoise's compressed+manifest storage pre-computes gzip/brotli variants at
# collectstatic time and rewrites asset filenames with a content hash so the
# response can carry an immutable long-cache header. Enabled only in production
# because it fails collectstatic when any {% static %} reference cannot be
# resolved, which is hostile in local dev where frontend/dist may not exist yet.
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
