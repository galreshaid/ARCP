"""
AAML RadCore Platform - Base Settings
"""
import os
import importlib.util
from pathlib import Path
from decouple import config

# Build paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env_bool(name, default=False):
    raw_value = config(name, default=None)
    if raw_value is None:
        return default

    normalized = str(raw_value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", "release"}:
        return False

    return default


def build_database_settings():
    engine = config('DB_ENGINE', default='sqlite').strip().lower()

    if engine in {'sqlite', 'sqlite3'}:
        sqlite_name = config('SQLITE_NAME', default='db.sqlite3').strip() or 'db.sqlite3'
        return {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': BASE_DIR / sqlite_name,
            }
        }

    if engine in {'postgres', 'postgresql', 'postgresql_psycopg2'}:
        return {
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': config('DB_NAME', default='aip_db'),
                'USER': config('DB_USER', default='postgres'),
                'PASSWORD': config('DB_PASSWORD', default='postgres'),
                'HOST': config('DB_HOST', default='localhost'),
                'PORT': config('DB_PORT', default='5432'),
            }
        }

    raise ValueError(f'Unsupported DB_ENGINE: {engine}')

# Security
SECRET_KEY = config('SECRET_KEY', default='django-insecure-development-key')
DEBUG = env_bool('DEBUG', default=False)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=lambda v: [s.strip() for s in v.split(',')])

# Application definition
DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

THIRD_PARTY_APPS = [
    'rest_framework',
    'corsheaders',
    'django_extensions',
    'drf_spectacular',
]

LOCAL_APPS = [
    'apps.core',
    'apps.users',
    #'apps.audit',
    'apps.protocols',
    'apps.hl7_core',
    'apps.qc',
    #'apps.contrast',
    #'apps.hl7',
    #'apps.reporting',
    #'apps.communication',
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    #'apps.audit.middleware.AuditMiddleware',  # Custom audit tracking
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.users.context_processors.inbox_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database
DATABASES = build_database_settings()

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static'] if (BASE_DIR / 'static').exists() else []
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Media files
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Evidence storage (QC screenshots)
EVIDENCE_ROOT = MEDIA_ROOT / 'evidence'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Custom User Model
AUTH_USER_MODEL = 'users.User'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'
SESSION_COOKIE_AGE = 900
SESSION_SAVE_EVERY_REQUEST = True

LDAP_AUTH_ENABLED = env_bool('LDAP_AUTH_ENABLED', default=False)
LDAP_SERVER_URI = config('LDAP_SERVER_URI', default='').strip()
LDAP_BIND_DN = config('LDAP_BIND_DN', default='').strip()
LDAP_BIND_PASSWORD = config('LDAP_BIND_PASSWORD', default='').strip()
LDAP_USER_SEARCH_BASE = config('LDAP_USER_SEARCH_BASE', default='').strip()
LDAP_USER_SEARCH_FILTER = config(
    'LDAP_USER_SEARCH_FILTER',
    default='(sAMAccountName=%(user)s)',
).strip()
LDAP_LOGIN_ATTRIBUTE = config('LDAP_LOGIN_ATTRIBUTE', default='sAMAccountName').strip()
LDAP_EMAIL_ATTRIBUTE = config('LDAP_EMAIL_ATTRIBUTE', default='mail').strip()
LDAP_FIRST_NAME_ATTRIBUTE = config('LDAP_FIRST_NAME_ATTRIBUTE', default='givenName').strip()
LDAP_LAST_NAME_ATTRIBUTE = config('LDAP_LAST_NAME_ATTRIBUTE', default='sn').strip()
LDAP_START_TLS = env_bool('LDAP_START_TLS', default=False)
LDAP_AUTH_AVAILABLE = bool(
    importlib.util.find_spec('ldap3') or importlib.util.find_spec('ldap')
)
AUTHENTICATION_BACKENDS = [
    'apps.users.auth_backends.OptionalLDAPBackend',
    'apps.users.auth_backends.LocalEmailOrUsernameBackend',
]

# Permission model configuration:
# - False: enforce explicit Django permissions from groups/user assignments.
# - True: additionally allow legacy role-based fallback from ROLE_PERMISSIONS.
USE_ROLE_PERMISSION_FALLBACK = env_bool('USE_ROLE_PERMISSION_FALLBACK', default=False)

# Default group bootstrap behavior after migrations:
# - False: only set permissions when creating missing default groups.
# - True: force-reset default group permissions on every migrate/post_migrate event.
SYNC_DEFAULT_GROUP_PERMISSIONS = env_bool('SYNC_DEFAULT_GROUP_PERMISSIONS', default=False)

# REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

# Celery Configuration
CELERY_BROKER_URL = config('CELERY_BROKER_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = config('CELERY_RESULT_BACKEND', default='redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE

# HL7 Configuration
HL7_MIRTH_HOST = config('HL7_MIRTH_HOST', default='localhost')
HL7_MIRTH_PORT = config('HL7_MIRTH_PORT', default=6661, cast=int)
HL7_LISTENER_HOST = config('HL7_LISTENER_HOST', default='0.0.0.0')
HL7_LISTENER_PORT = config('HL7_LISTENER_PORT', default=2575, cast=int)
ICD10_XML_PATH = config('ICD10_XML_PATH', default='')
HL7_SENDING_APPLICATION = 'AIP'
HL7_SENDING_FACILITY = config('HL7_FACILITY', default='HOSPITAL')
HL7_RETRY_MAX_ATTEMPTS = 3
HL7_RETRY_DELAY_SECONDS = 60

# Deep Link Configuration
DEEPLINK_SECRET_KEY = config('DEEPLINK_SECRET_KEY', default=SECRET_KEY)
DEEPLINK_EXPIRY_HOURS = 24
DEEPLINK_ALGORITHM = 'HS256'

# Audit Settings
AUDIT_RETENTION_DAYS = 2555  # ~7 years
EVIDENCE_ACCESS_LOG_ENABLED = True

# QC Settings
QC_EVIDENCE_MAX_FILE_SIZE_MB = 10
QC_EVIDENCE_ALLOWED_FORMATS = ['.png', '.jpg', '.jpeg', '.pdf']
PACS_STUDY_URL_TEMPLATE = config(
    'PACS_STUDY_URL_TEMPLATE',
    default='pacs://study/{accession}',
).strip()

# Contrast Safety Settings
CONTRAST_VOLUME_WARNING_THRESHOLD_ML = 150
CONTRAST_VOLUME_MAX_THRESHOLD_ML = 200
CONTRAST_REQUIRE_REACTION_DOCUMENTATION = True

# Logging
LOG_DIR = BASE_DIR / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'file': {
            'class': 'logging.FileHandler',
            'filename': LOG_DIR / 'aip.log',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console', 'file'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'apps.hl7': {
            'handlers': ['console', 'file'],
            'level': 'DEBUG',
            'propagate': False,
        },
    },
}
