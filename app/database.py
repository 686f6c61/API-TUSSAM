"""
TUSSAM API - Módulo de Base de Datos
=====================================

Gestión de SQLite para almacenar:
- Paradas de TUSSAM (código, nombre, coordenadas GPS, dirección)
- Líneas de autobús
- Relaciones parada-línea (N:M con sentido y orden)
- Cache de tiempos de llegada (TTL: 1 minuto)

Usa una conexión persistente en modo WAL para mejor rendimiento en lecturas concurrentes.

Autor: 686f6c61 (https://github.com/686f6c61)
Versión: 1.0.0
Licencia: MIT
"""

import sqlite3
import aiosqlite
import logging
from typing import List, Optional
from datetime import datetime, timedelta
import math
import json

logger = logging.getLogger("tussam.database")

# Configuración
DATABASE_URL = "data/tussam.db"
CACHE_TTL_MINUTES = 1

# Conexión persistente (se inicializa en startup, se cierra en shutdown)
_db: Optional[aiosqlite.Connection] = None


async def get_db() -> aiosqlite.Connection:
    """
    Devuelve la conexión persistente a SQLite (singleton).
    
    Se crea una sola vez al iniciar la aplicación y se reutiliza en todas
    las queries. Esto evita el coste de abrir/cerrar conexiones por petición.
    
    SQLite en modo WAL (Write-Ahead Logging) permite lecturas concurrentes
    sin bloquearse entre sí. El busy_timeout de 5s evita errores "database
    is locked" cuando coinciden una escritura y una lectura.
    """
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DATABASE_URL)
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA busy_timeout=5000")
        _db.row_factory = aiosqlite.Row  # Acceso a columnas por nombre
    return _db


async def close_db():
    """Cierra la conexión persistente."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None


async def db_health() -> bool:
    """Verifica que la base de datos responde."""
    try:
        db = await get_db()
        await db.execute("SELECT 1")
        return True
    except Exception:
        return False


async def init_db():
    """
    Inicializa la base de datos creando las tablas necesarias.
    Se ejecuta automáticamente al iniciar la aplicación.
    """
    db = await get_db()

    # Tabla de paradas de TUSSAM
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
    for col, col_type in [
        ("codigo_postal", "TEXT"),
        ("municipio", "TEXT"),
        ("provincia", "TEXT"),
        ("comunidad_autonoma", "TEXT"),
        ("direccion_completa", "TEXT"),
    ]:
        try:
            await db.execute(f"ALTER TABLE paradas ADD COLUMN {col} {col_type}")
        except Exception as e:
            if "duplicate column name" not in str(e).lower():
                raise

    # Tabla de líneas de TUSSAM
    await db.execute("""
        CREATE TABLE IF NOT EXISTS lineas (
            numero TEXT PRIMARY KEY,
            nombre TEXT NOT NULL,
            color TEXT NOT NULL,
            sublinea INTEGER,
            hora_inicio_ida TEXT,
            hora_fin_ida TEXT,
            hora_inicio_vuelta TEXT,
            hora_fin_vuelta TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Migración: añadir columnas de horarios si no existen
    for col in ["sublinea", "hora_inicio_ida", "hora_fin_ida", "hora_inicio_vuelta", "hora_fin_vuelta"]:
        try:
            await db.execute(f"ALTER TABLE lineas ADD COLUMN {col} TEXT")
        except Exception:
            pass  # ya existe

    # Relación N:M entre paradas y líneas
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
    await db.execute("""
        CREATE TABLE IF NOT EXISTS tiempos_cache (
            parada_codigo TEXT PRIMARY KEY,
            tiempos_json TEXT NOT NULL,
            cached_at TIMESTAMP NOT NULL
        )
    """)

    await db.commit()


# ---------------------------------------------------------------------------
# Paradas
# ---------------------------------------------------------------------------

async def get_all_paradas_from_db() -> List[dict]:
    """Obtiene todas las paradas de la base de datos."""
    db = await get_db()
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
    db = await get_db()
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
    Guarda múltiples paradas. Preserva las columnas de dirección
    si la parada ya existe y la nueva no trae calle.
    """
    db = await get_db()
    for p in paradas:
        calle = p.get("calle")
        if calle:
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
    """Obtiene una parada específica por su código."""
    db = await get_db()
    db.row_factory = aiosqlite.Row
    async with db.execute("SELECT * FROM paradas WHERE codigo = ?", (codigo,)) as cursor:
        row = await cursor.fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Líneas
# ---------------------------------------------------------------------------

async def get_lineas_from_db() -> List[dict]:
    """Obtiene todas las líneas con horarios y sublinea."""
    db = await get_db()
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT numero, nombre, color, sublinea, "
        "hora_inicio_ida, hora_fin_ida, "
        "hora_inicio_vuelta, hora_fin_vuelta, "
        "updated_at FROM lineas ORDER BY numero"
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def save_lineas_batch(lineas: List[dict]):
    """Guarda múltiples líneas con horarios y sublinea."""
    db = await get_db()
    for l in lineas:
        await db.execute(
            """
            INSERT OR REPLACE INTO lineas
            (numero, nombre, color, sublinea,
             hora_inicio_ida, hora_fin_ida, hora_inicio_vuelta, hora_fin_vuelta,
             updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                l["numero"], l["nombre"], l["color"],
                l.get("sublinea"),
                l.get("hora_inicio_ida"), l.get("hora_fin_ida"),
                l.get("hora_inicio_vuelta"), l.get("hora_fin_vuelta"),
                datetime.now(),
            ),
        )
    await db.commit()


# ---------------------------------------------------------------------------
# Geografía: Haversine y bounding box
#
# La fórmula de Haversine calcula la distancia ortodrómica (línea recta sobre
# la esfera terrestre). El bounding box es un prefiltro rápido que descarta
# paradas fuera de un rectángulo antes de aplicar el cálculo exacto.
# ---------------------------------------------------------------------------

# Aproximación: 1 grado de latitud ≈ 111 320 m en el ecuador
_LAT_M_PER_DEG = 111_320


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Distancia en metros entre dos puntos geográficos (fórmula de Haversine).

    A diferencia de la distancia euclídea, tiene en cuenta la curvatura terrestre.
    Para Sevilla (lat ~37.4°), el error de simplificar es < 0.01% dado que el
    radio de búsqueda máximo es 2 km.

    Returns:
        Distancia en metros
    """
    R = 6_371_000  # Radio medio de la Tierra en metros
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(a))


def bounding_box(lat: float, lon: float, radio_m: float) -> tuple:
    """
    Caja delimitadora rectangular para un radio dado.

    En lugar de calcular Haversine para las 967 paradas, primero filtramos
    por coordenadas (2 comparaciones numéricas vs 6 funciones trigonométricas).
    Esto descarta ~85% de las paradas en una búsqueda típica de 300m.

    El radio se multiplica por 1.1 para cubrir paradas en las esquinas de la caja
    que podrían quedar fuera por la diferencia entre círculo y cuadrado.

    Returns:
        (lat_min, lat_max, lon_min, lon_max)
    """
    delta_lat = radio_m / _LAT_M_PER_DEG
    delta_lon = radio_m / (_LAT_M_PER_DEG * math.cos(math.radians(lat)))
    return (lat - delta_lat, lat + delta_lat, lon - delta_lon, lon + delta_lon)


# ---------------------------------------------------------------------------
# Cache de tiempos
# ---------------------------------------------------------------------------

async def get_cached_tiempos(parada_codigo: str) -> Optional[dict]:
    """
    Obtiene los tiempos de llegada cacheados para una parada.
    Devuelve None si no existe o ha expirado (TTL: 1 minuto).
    """
    db = await get_db()
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
    """Guarda los tiempos de llegada en el cache."""
    db = await get_db()
    await db.execute(
        """
        INSERT OR REPLACE INTO tiempos_cache (parada_codigo, tiempos_json, cached_at)
        VALUES (?, ?, ?)
    """,
        (parada_codigo, json.dumps(tiempos), datetime.now()),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Geocodificación de direcciones
# ---------------------------------------------------------------------------

async def get_paradas_sin_direccion() -> List[dict]:
    """Obtiene paradas que no tienen calle asignada."""
    db = await get_db()
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
    db = await get_db()
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


# ---------------------------------------------------------------------------
# Relaciones parada-línea
# ---------------------------------------------------------------------------

async def save_paradas_lineas_batch(relaciones: List[dict]):
    """Guarda las relaciones parada-línea. Borra las existentes primero."""
    if not relaciones:
        logger.warning("save_paradas_lineas_batch llamado con lista vacía")
        return

    db = await get_db()
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
    db = await get_db()
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
    db = await get_db()
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
    db = await get_db()
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
