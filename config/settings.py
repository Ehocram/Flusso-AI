"""
Configurazione Django — ISEO AI · Gestione Flusso.

Le impostazioni sensibili sono lette da variabili d'ambiente (vedi .env.example).
In assenza di configurazione il progetto parte in modalita' sviluppo su SQLite,
cosi' da essere eseguibile senza alcuna infrastruttura. In produzione si
attivano PostgreSQL, cookie sicuri e hardening.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(nome: str, default: bool = False) -> bool:
    return os.environ.get(nome, str(default)).lower() in ("1", "true", "yes", "on")


def env_list(nome: str, default: str = "") -> list[str]:
    raw = os.environ.get(nome, default)
    return [v.strip() for v in raw.split(",") if v.strip()]


# --- Sicurezza di base -------------------------------------------------------

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-only-insecure-key-da-NON-usare-in-produzione",
)

DEBUG = env_bool("DJANGO_DEBUG", default=True)

# In produzione impostare DJANGO_ALLOWED_HOSTS (es. "flussoai.iseo.local").
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")

# Necessario dietro reverse proxy (Caddy/Nginx) con HTTPS terminato sul proxy.
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


# --- Applicazioni ------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "flusso",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "accounts.middleware.ForzaCambioPasswordMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "flusso.context_processors.statistiche_globali",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# --- Database ----------------------------------------------------------------
# PostgreSQL se POSTGRES_DB e' definito, altrimenti SQLite (sviluppo).

if os.environ.get("POSTGRES_DB"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ["POSTGRES_DB"],
            "USER": os.environ.get("POSTGRES_USER", "iseo"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POSTGRES_HOST", "db"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


# --- Autenticazione ----------------------------------------------------------

AUTH_USER_MODEL = "accounts.Utente"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "flusso:dashboard"
LOGOUT_REDIRECT_URL = "login"

AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]


# --- Internazionalizzazione --------------------------------------------------

LANGUAGE_CODE = "it"
TIME_ZONE = "Europe/Rome"
USE_I18N = True
USE_TZ = True


# --- File statici ------------------------------------------------------------

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --- Cookie / hardening produzione -------------------------------------------

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SESSION_COOKIE_HTTPONLY = True
    X_FRAME_OPTIONS = "DENY"


# --- Logging (audit applicativo su stdout, raccoglibile dal SIEM) ------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "audit": {"format": "%(asctime)s AUDIT %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
        "audit_console": {"class": "logging.StreamHandler", "formatter": "audit"},
    },
    "loggers": {
        "flusso.audit": {"handlers": ["audit_console"], "level": "INFO", "propagate": False},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
}
