"""
ASGI config for config project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application


def _default_settings_module() -> str:
    if os.getenv('DJANGO_SETTINGS_MODULE'):
        return os.getenv('DJANGO_SETTINGS_MODULE')

    if os.getenv('RENDER') or os.getenv('RENDER_EXTERNAL_HOSTNAME'):
        return 'config.settings.prod'

    return 'config.settings.dev'


os.environ.setdefault('DJANGO_SETTINGS_MODULE', _default_settings_module())

application = get_asgi_application()
