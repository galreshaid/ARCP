"""
Production Settings
"""
from .base import *
import dj_database_url
from django.core.exceptions import ImproperlyConfigured

DEBUG = False

ALLOWED_HOSTS = [
    host.strip()
    for host in config('ALLOWED_HOSTS', default='.onrender.com,localhost,127.0.0.1').split(',')
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in config('CSRF_TRUSTED_ORIGINS', default='https://*.onrender.com').split(',')
    if origin.strip()
]

# Security settings
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Database - supports Render DATABASE_URL and explicit DB_* variables.
DATABASE_URL = config('DATABASE_URL', default='').strip()
if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            ssl_require=True,
        )
    }
else:
    db_name = config('DB_NAME', default='').strip()
    if not db_name:
        raise ImproperlyConfigured(
            'Production database is not configured. Set DATABASE_URL (recommended on Render) '
            'or DB_NAME/DB_USER/DB_PASSWORD/DB_HOST/DB_PORT.'
        )

    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': db_name,
            'USER': config('DB_USER', default=''),
            'PASSWORD': config('DB_PASSWORD', default=''),
            'HOST': config('DB_HOST', default=''),
            'PORT': config('DB_PORT', default='5432'),
        }
    }

# Static files
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Logging - console only in container runtime.
LOGGING['handlers'] = {
    'console': {
        'class': 'logging.StreamHandler',
        'formatter': 'verbose',
    }
}
LOGGING['root']['handlers'] = ['console']
LOGGING['loggers']['django']['handlers'] = ['console']
LOGGING['loggers']['apps.hl7']['handlers'] = ['console']
LOGGING['root']['level'] = 'INFO'
