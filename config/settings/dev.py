"""
Development Settings - Simplified
"""
from .base import *

DEBUG = True
ALLOWED_HOSTS = ['*']

# Database comes from base.py and defaults to SQLite in development.

# CORS
CORS_ALLOW_ALL_ORIGINS = True

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

