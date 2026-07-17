"""
TUSSAM API - Utilidades de configuración por entorno
=====================================================

Funciones para leer variables de entorno de forma tolerante a fallos. La política
común es: si una variable tiene un valor inválido, se registra un aviso y se cae
al valor por defecto en lugar de tumbar el arranque de la aplicación.

Centralizar estos ayudantes en un único módulo evita que sus implementaciones
(duplicadas antes en varios ficheros) diverjan con el tiempo.

Autor: 686f6c61 (https://github.com/686f6c61)
Versión: 2.0.0
Licencia: PolyForm Noncommercial 1.0.0 (uso no comercial)
"""

import logging
import os

logger = logging.getLogger("tussam.env")


def env_bool(name: str, default: bool = False) -> bool:
    """Lee un booleano de entorno de forma explícita y predecible.

    Se consideran verdaderos los valores ``1``, ``true``, ``yes`` y ``on``
    (ignorando mayúsculas y espacios). Cualquier otro valor es falso.

    Args:
        name: Nombre de la variable de entorno.
        default: Valor a devolver si la variable no está definida.

    Returns:
        El booleano interpretado.
    """
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_csv(name: str, default: str = "") -> list[str]:
    """Lee una lista separada por comas de una variable de entorno.

    Los elementos vacíos se descartan y se recortan los espacios.

    Args:
        name: Nombre de la variable de entorno.
        default: Cadena por defecto si la variable no está definida.

    Returns:
        Lista de cadenas no vacías.
    """
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def env_int(name: str, default: int, minimum: int = 1) -> int:
    """Lee un entero de entorno con un mínimo para evitar valores peligrosos.

    Un valor no numérico se ignora (con aviso) y se usa el default.

    Args:
        name: Nombre de la variable de entorno.
        default: Valor por defecto si falta o es inválida.
        minimum: Cota inferior aplicada al resultado.

    Returns:
        El entero interpretado, nunca por debajo de ``minimum``.
    """
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        logger.warning("%s inválido; usando %d", name, default)
        return default
    return max(minimum, value)


def env_float(name: str, default: float, minimum: float = 0.0) -> float:
    """Lee un float de entorno con un mínimo (para pausas y timeouts).

    Args:
        name: Nombre de la variable de entorno.
        default: Valor por defecto si falta o es inválida.
        minimum: Cota inferior aplicada al resultado.

    Returns:
        El float interpretado, nunca por debajo de ``minimum``.
    """
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        logger.warning("%s inválido; usando %.2f", name, default)
        return default
    return max(minimum, value)
