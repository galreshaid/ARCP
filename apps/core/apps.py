"""
Core App Configuration
"""
from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.core'
    verbose_name = 'Core'

    def ready(self):
        # Register core signal handlers (including pre-migrate DB sequence repair).
        from . import signals  # noqa: F401
