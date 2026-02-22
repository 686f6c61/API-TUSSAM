"""
TUSSAM API - Módulo de Base de Datos
=====================================

Gestión de SQLite para almacenar:
- Paradas de TUSSAM (código, nombre, coordenadas GPS)
- Líneas de autobús
- Cache de tiempos de llegada (TTL: 1 minuto)
- Cache de direcciones geocodificadas (TTL: 30 días)

Autor: 686f6c61 (https://github.com/686f6c61)
Versión: 1.1.0
Licencia: MIT
"""

import sqlite3
import aiosqlite
import logging
import os
from typing import List, Optional
from datetime import datetime, timedelta
import math
import json

logger = logging.getLogger("tussam.database")

# Configuración de la base de datos
def _resolve_database_path() -> str:
    """
    Soporta rutas SQLite directas y URLs estilo sqlite+aiosqlite:///... .
    """
    raw_value = os.getenv("DATABASE_URL", "data/tussam.db").strip()
    if raw_value.startswith("sqlite+aiosqlite:///"):
        return raw_value.replace("sqlite+aiosqlite:///", "", 1)
    if raw_value.startswith("sqlite:///"):
        return raw_value.replace("sqlite:///", "", 1)
    return raw_value


DATABASE_URL = _resolve_database_path()

# Tiempo de vida del cache (en minutos)
# Los tiempos de llegada cambian frecuentemente, 1 minuto es razonable
CACHE_TTL_MINUTES = 1

# Tiempo de vida del cache de direcciones (en días)
# Las direcciones no cambian, 30 días es suficiente
CACHE_DIRECCIONES_TTL_DIAS = 30


async def init_db():
    """
    Inicializa la base de datos creando las tablas necesarias.
    Se ejecuta automáticamente al iniciar la aplicación.
    """
    async with aiosqlite.connect(DATABASE_URL) as db:
        # Optimizaciones SQLite
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")

        # Tabla de paradas de TUSSAM
        # Contiene: código, nombre, coordenadas GPS, dirección
        await db.execute("""
            CREATE TABLE IF NOT EXISTS paradas (
                codigo TEXT PRIMARY KEY,
                nombre TEXT NOT NULL,
                latitud REAL NOT NULL,
                longitud REAL NOT NULL,
                calle TEXT,
                numero TEXT,
                codigo_postal TEXT,
                municipio TEXT,
                provincia TEXT,
                comunidad_autonoma TEXT,
                direccion_completa TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migración: añadir columnas de dirección si no existen
        # (para DBs creadas antes de tener estas columnas)
        for col, col_type in [
            ("codigo_postal", "TEXT"),
            ("municipio", "TEXT"),
            ("provincia", "TEXT"),
            ("comunidad_autonoma", "TEXT"),
            ("direccion_completa", "TEXT"),
        ]:
            try:
                await db.execute(
                    f"ALTER TABLE paradas ADD COLUMN {col} {col_type}"
                )
            except Exception as e:
                if "duplicate column name" in str(e).lower():
                    pass  # La columna ya existe
                else:
                    logger.error("Error añadiendo columna %s a paradas: %s", col, e)
                    raise

        # Tabla de líneas de TUSSAM
        await db.execute("""
            CREATE TABLE IF NOT EXISTS lineas (
                numero TEXT PRIMARY KEY,
                nombre TEXT NOT NULL,
                color TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Relación N:M entre paradas y líneas
        # Una parada puede tener varias líneas y viceversa
        await db.execute("""
            CREATE TABLE IF NOT EXISTS paradas_lineas (
                parada_codigo TEXT NOT NULL,
                linea_numero TEXT NOT NULL,
                sentido INTEGER NOT NULL,
                orden INTEGER NOT NULL,
                PRIMARY KEY (parada_codigo, linea_numero, sentido),
                FOREIGN KEY (parada_codigo) REFERENCES paradas(codigo),
                FOREIGN KEY (linea_numero) REFERENCES lineas(numero)
            )
        """)

        # Cache de tiempos de llegada
        # Evita hacer demasiadas peticiones a la API de TUSSAM
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tiempos_cache (
                parada_codigo TEXT PRIMARY KEY,
                tiempos_json TEXT NOT NULL,
                cached_at TIMESTAMP NOT NULL
            )
        """)

        # Cache de direcciones geocodificadas
        # Almacena calle y número obtenidos de OpenStreetMap/Nominatim
        await db.execute("""
            CREATE TABLE IF NOT EXISTS direcciones_cache (
                latitud REAL NOT NULL,
                longitud REAL NOT NULL,
                direccion_json TEXT NOT NULL,
                cached_at TIMESTAMP NOT NULL,
                PRIMARY KEY (latitud, longitud)
            )
        """)
        await db.commit()


async def get_all_paradas_from_db() -> List[dict]:
    """
    Obtiene todas las paradas de la base de datos.

    Returns:
        List[dict]: Lista de paradas con código, nombre y coordenadas
    """
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM paradas ORDER BY codigo") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def save_parada(
    codigo: str,
    nombre: str,
    latitud: float,
    longitud: float,
    calle: str = None,
    numero: str = None,
):
    """Guarda una sola parada en la base de datos."""
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO paradas (codigo, nombre, latitud, longitud, calle, numero, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (codigo, nombre, latitud, longitud, calle, numero, datetime.now()),
        )
        await db.commit()


async def save_paradas_batch(paradas: List[dict]):
    """
    Guarda múltiples paradas en una sola operación.
    Usa ON CONFLICT para preservar las columnas de dirección existentes.
    """
    async with aiosqlite.connect(DATABASE_URL) as db:
        for p in paradas:
            calle = p.get("calle")
            if calle:
                # Parada con dirección: actualizar todo incluyendo calle
                await db.execute(
                    """
                    INSERT INTO paradas (codigo, nombre, latitud, longitud, calle, numero, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(codigo) DO UPDATE SET
                        nombre = excluded.nombre,
                        latitud = excluded.latitud,
                        longitud = excluded.longitud,
                        calle = excluded.calle,
                        numero = excluded.numero,
                        updated_at = excluded.updated_at
                """,
                    (p["codigo"], p["nombre"], p["latitud"], p["longitud"],
                     calle, p.get("numero"), datetime.now()),
                )
            else:
                # Parada sin dirección: preservar columnas de dirección existentes
                await db.execute(
                    """
                    INSERT INTO paradas (codigo, nombre, latitud, longitud, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(codigo) DO UPDATE SET
                        nombre = excluded.nombre,
                        latitud = excluded.latitud,
                        longitud = excluded.longitud,
                        updated_at = excluded.updated_at
                """,
                    (p["codigo"], p["nombre"], p["latitud"], p["longitud"], datetime.now()),
                )
        await db.commit()


async def get_parada_by_codigo(codigo: str) -> Optional[dict]:
    """
    Obtiene una parada específica por su código.

    Args:
        codigo: Código de la parada (ej: "43", "183")

    Returns:
        Dict con los datos de la parada o None si no existe
    """
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM paradas WHERE codigo = ?", (codigo,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_lineas_from_db() -> List[dict]:
    """Obtiene todas las líneas de la base de datos."""
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM lineas ORDER BY numero") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def save_lineas_batch(lineas: List[dict]):
    """Guarda múltiples líneas en una sola operación."""
    async with aiosqlite.connect(DATABASE_URL) as db:
        for l in lineas:
            await db.execute(
                """
                INSERT OR REPLACE INTO lineas (numero, nombre, color, updated_at)
                VALUES (?, ?, ?, ?)
            """,
                (l["numero"], l["nombre"], l["color"], datetime.now()),
            )
        await db.commit()


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calcula la distancia entre dos puntos usando la fórmula de Haversine.

    Args:
        lat1, lon1: Coordenadas del primer punto
        lat2, lon2: Coordenadas del segundo punto

    Returns:
        Distancia en metros
    """
    R = 6371000  # Radio de la Tierra en metros
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(a))


async def get_cached_tiempos(parada_codigo: str) -> Optional[dict]:
    """
    Obtiene los tiempos de llegada cacheados para una parada.

    Args:
        parada_codigo: Código de la parada

    Returns:
        Tiempos cacheados o None si ha expirado/no existe
    """
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tiempos_cache WHERE parada_codigo = ?", (parada_codigo,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                cached_at = datetime.fromisoformat(row["cached_at"])
                if datetime.now() - cached_at < timedelta(minutes=CACHE_TTL_MINUTES):
                    try:
                        return json.loads(row["tiempos_json"])
                    except json.JSONDecodeError:
                        logger.error("Cache corrupto para parada %s, eliminando", parada_codigo)
                        await db.execute(
                            "DELETE FROM tiempos_cache WHERE parada_codigo = ?", (parada_codigo,)
                        )
                        await db.commit()
    return None


async def save_tiempos_cache(parada_codigo: str, tiempos: dict):
    """
    Guarda los tiempos de llegada en el cache.
    """
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO tiempos_cache (parada_codigo, tiempos_json, cached_at)
            VALUES (?, ?, ?)
        """,
            (parada_codigo, json.dumps(tiempos), datetime.now()),
        )
        await db.commit()


async def get_cached_direccion(lat: float, lon: float) -> Optional[dict]:
    """
    Obtiene la dirección cacheada para unas coordenadas.

    Args:
        lat, lon: Coordenadas

    Returns:
        Dirección cacheada o None si ha expirado/no existe
    """
    lat_r = round(lat, 4)
    lon_r = round(lon, 4)
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM direcciones_cache WHERE latitud = ? AND longitud = ?",
            (lat_r, lon_r),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                cached_at = datetime.fromisoformat(row["cached_at"])
                if datetime.now() - cached_at < timedelta(
                    days=CACHE_DIRECCIONES_TTL_DIAS
                ):
                    try:
                        return json.loads(row["direccion_json"])
                    except json.JSONDecodeError:
                        logger.error("Cache dirección corrupto para (%f, %f), eliminando", lat_r, lon_r)
                        await db.execute(
                            "DELETE FROM direcciones_cache WHERE latitud = ? AND longitud = ?",
                            (lat_r, lon_r),
                        )
                        await db.commit()
    return None


async def save_direccion_cache(lat: float, lon: float, direccion: dict):
    """
    Guarda la dirección geocodificada en el cache.
    Las direcciones se cachean por 30 días ya que no cambian.
    """
    lat_r = round(lat, 4)
    lon_r = round(lon, 4)
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO direcciones_cache (latitud, longitud, direccion_json, cached_at)
            VALUES (?, ?, ?, ?)
        """,
            (lat_r, lon_r, json.dumps(direccion), datetime.now()),
        )
        await db.commit()


async def get_paradas_sin_direccion() -> List[dict]:
    """Obtiene paradas que no tienen calle asignada."""
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT codigo, nombre, latitud, longitud FROM paradas WHERE calle IS NULL OR calle = ''"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def update_parada_direccion(
    codigo: str, calle: str, numero: str,
    codigo_postal: str, municipio: str, provincia: str,
    comunidad_autonoma: str, direccion_completa: str,
):
    """Actualiza todos los campos de dirección de una parada."""
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(
            """
            UPDATE paradas
            SET calle = ?, numero = ?, codigo_postal = ?,
                municipio = ?, provincia = ?, comunidad_autonoma = ?,
                direccion_completa = ?
            WHERE codigo = ?
        """,
            (calle, numero, codigo_postal, municipio, provincia,
             comunidad_autonoma, direccion_completa, codigo),
        )
        await db.commit()


async def save_paradas_lineas_batch(relaciones: List[dict]):
    """Guarda las relaciones parada-línea en lote. Borra las existentes primero."""
    if not relaciones:
        logger.warning("save_paradas_lineas_batch llamado con lista vacía, no se borra nada")
        return

    async with aiosqlite.connect(DATABASE_URL) as db:
        try:
            await db.execute("DELETE FROM paradas_lineas")
            for r in relaciones:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO paradas_lineas (parada_codigo, linea_numero, sentido, orden)
                    VALUES (?, ?, ?, ?)
                """,
                    (r["parada_codigo"], r["linea_numero"], r["sentido"], r["orden"]),
                )
            await db.commit()
        except Exception:
            await db.rollback()
            logger.error("Error guardando paradas_lineas, rollback ejecutado")
            raise


async def get_lineas_de_parada(parada_codigo: str) -> List[str]:
    """Obtiene las líneas que pasan por una parada."""
    async with aiosqlite.connect(DATABASE_URL) as db:
        async with db.execute(
            "SELECT DISTINCT linea_numero FROM paradas_lineas WHERE parada_codigo = ? ORDER BY linea_numero",
            (parada_codigo,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]


async def get_sentidos_for_parada(parada_codigo: str) -> dict:
    """
    Devuelve los sentidos de cada línea en una parada.

    Returns:
        Dict {linea_numero: [sentido, ...]} ej: {"01": [1], "C4": [1, 2]}
    """
    async with aiosqlite.connect(DATABASE_URL) as db:
        async with db.execute(
            "SELECT linea_numero, sentido FROM paradas_lineas WHERE parada_codigo = ? ORDER BY linea_numero, sentido",
            (parada_codigo,),
        ) as cursor:
            rows = await cursor.fetchall()
            result = {}
            for linea, sentido in rows:
                result.setdefault(linea, []).append(sentido)
            return result


async def get_paradas_de_linea(linea_numero: str) -> List[dict]:
    """Obtiene las paradas de una línea ordenadas por sentido y orden."""
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT pl.sentido, pl.orden, p.*
            FROM paradas_lineas pl
            JOIN paradas p ON p.codigo = pl.parada_codigo
            WHERE pl.linea_numero = ?
            ORDER BY pl.sentido, pl.orden
        """,
            (linea_numero,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
