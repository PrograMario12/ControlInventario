"""Configuración de la aplicación, leída de variables de entorno o del archivo .env."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


class ConfigError(Exception):
    """Falta una configuración obligatoria. El mensaje es apto para mostrarse al usuario."""


def database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise ConfigError(
            "Falta la variable DATABASE_URL.\n\n"
            "Copia el archivo .env.example como .env y escribe ahí la dirección de tu base de datos."
        )
    return normalize_database_url(url)


def normalize_database_url(url: str) -> str:
    """Usa el driver psycopg 3 aunque la URL venga como postgresql:// o postgres:// (p. ej. la de Neon)."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url
