from .base import *

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-local-secret-key")

ALLOWED_HOSTS = env.list(
    "DJANGO_ALLOWED_HOSTS",
    default=["localhost", "0.0.0.0", "127.0.0.1", "192.168.31.30"],
)

# Email
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#email-backend
EMAIL_BACKEND = env(
    "DJANGO_EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)

REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    'DEFAULT_THROTTLE_RATES': {
        **REST_FRAMEWORK.get('DEFAULT_THROTTLE_RATES', {}),
        'auth': env('DJANGO_LOCAL_AUTH_THROTTLE_RATE', default='1000/hour'),
    },
}
