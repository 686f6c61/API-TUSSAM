"""
Tests para app/database.py - Capa de base de datos.
"""

import pytest
import json
from datetime import datetime, timedelta
from app import database


# ── init_db ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_init_db_creates_tables(db_ready):
    """init_db debe crear las 5 tablas necesarias."""
    import aiosqlite

    async with aiosqlite.connect(db_ready) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in await cursor.fetchall()]

    expected = ["direcciones_cache", "lineas", "paradas", "paradas_lineas", "tiempos_cache"]
    assert tables == expected


@pytest.mark.asyncio
async def test_init_db_idempotent(db_ready):
    """Llamar init_db dos veces no debe fallar."""
    await database.init_db()  # Segunda llamada


# ── Paradas ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_and_get_parada(db_ready):
    """Guardar una parada y recuperarla por código."""
    await database.save_parada("99", "Test Parada", 37.38, -5.98)
    result = await database.get_parada_by_codigo("99")
    assert result is not None
    assert result["codigo"] == "99"
    assert result["nombre"] == "Test Parada"
    assert result["latitud"] == 37.38


@pytest.mark.asyncio
async def test_get_parada_inexistente(db_ready):
    """Buscar parada que no existe debe devolver None."""
    result = await database.get_parada_by_codigo("NOEXISTE")
    assert result is None


@pytest.mark.asyncio
async def test_save_paradas_batch(db_with_paradas):
    """save_paradas_batch debe guardar múltiples paradas."""
    all_paradas = await database.get_all_paradas_from_db()
    assert len(all_paradas) == 3
    codigos = {p["codigo"] for p in all_paradas}
    assert codigos == {"43", "44", "252"}


@pytest.mark.asyncio
async def test_save_parada_upsert(db_ready):
    """Guardar la misma parada dos veces debe actualizar, no duplicar."""
    await database.save_parada("99", "Nombre Original", 37.38, -5.98)
    await database.save_parada("99", "Nombre Actualizado", 37.39, -5.99)
    result = await database.get_parada_by_codigo("99")
    assert result["nombre"] == "Nombre Actualizado"
    assert result["latitud"] == 37.39

    all_paradas = await database.get_all_paradas_from_db()
    assert len(all_paradas) == 1


@pytest.mark.asyncio
async def test_get_paradas_sin_direccion(db_with_paradas):
    """Debe devolver solo paradas sin calle."""
    sin_dir = await database.get_paradas_sin_direccion()
    assert len(sin_dir) == 1
    assert sin_dir[0]["codigo"] == "252"


@pytest.mark.asyncio
async def test_update_parada_direccion(db_with_paradas):
    """Actualizar dirección de una parada."""
    await database.update_parada_direccion(
        "252", "Av. Inmigrantes", "10", "41020",
        "Sevilla", "Sevilla", "Andalucia", "Av. Inmigrantes 10"
    )
    parada = await database.get_parada_by_codigo("252")
    assert parada["calle"] == "Av. Inmigrantes"
    assert parada["numero"] == "10"
    assert parada["direccion_completa"] == "Av. Inmigrantes 10"


# ── Líneas ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_and_get_lineas(db_with_lineas):
    """Guardar y recuperar líneas."""
    lineas = await database.get_lineas_from_db()
    assert len(lineas) == 2
    numeros = {l["numero"] for l in lineas}
    assert numeros == {"01", "C4"}


# ── Relaciones parada-línea ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_and_get_paradas_lineas(db_with_relations):
    """Las relaciones parada-línea deben guardarse correctamente."""
    lineas_43 = await database.get_lineas_de_parada("43")
    assert set(lineas_43) == {"01", "C4"}


@pytest.mark.asyncio
async def test_get_lineas_parada_sin_relaciones(db_with_paradas):
    """Parada sin relaciones debe devolver lista vacía."""
    lineas = await database.get_lineas_de_parada("43")
    assert lineas == []


@pytest.mark.asyncio
async def test_get_sentidos_for_parada(db_with_relations):
    """get_sentidos_for_parada debe devolver sentidos agrupados por línea."""
    sentidos = await database.get_sentidos_for_parada("43")
    # Parada 43: línea 01 sentido 1 y 2, línea C4 solo sentido 1
    assert sentidos["01"] == [1, 2]
    assert sentidos["C4"] == [1]


@pytest.mark.asyncio
async def test_get_sentidos_parada_sin_datos(db_ready):
    """Parada sin relaciones debe devolver dict vacío."""
    sentidos = await database.get_sentidos_for_parada("NOEXISTE")
    assert sentidos == {}


@pytest.mark.asyncio
async def test_get_paradas_de_linea(db_with_relations):
    """Debe devolver paradas ordenadas por sentido y orden."""
    paradas = await database.get_paradas_de_linea("01")
    assert len(paradas) >= 3
    # Verifica orden: sentido 1 primero, luego sentido 2
    sentidos = [p["sentido"] for p in paradas]
    assert sentidos == sorted(sentidos)


@pytest.mark.asyncio
async def test_save_paradas_lineas_batch_replaces(db_with_relations):
    """save_paradas_lineas_batch borra las existentes antes de insertar."""
    nuevas = [
        {"parada_codigo": "44", "linea_numero": "C4", "sentido": 2, "orden": 0},
    ]
    await database.save_paradas_lineas_batch(nuevas)
    # Las relaciones anteriores deben haber desaparecido
    lineas_43 = await database.get_lineas_de_parada("43")
    assert lineas_43 == []
    lineas_44 = await database.get_lineas_de_parada("44")
    assert lineas_44 == ["C4"]


# ── Haversine ────────────────────────────────────────────────────────

def test_haversine_mismo_punto():
    """Distancia entre un punto y sí mismo debe ser 0."""
    d = database.haversine(37.389, -5.984, 37.389, -5.984)
    assert d == 0.0


def test_haversine_distancia_conocida():
    """Distancia Sevilla centro a Triana ~1km."""
    d = database.haversine(37.3886, -5.9823, 37.3830, -5.9990)
    assert 1000 < d < 2000  # ~1.5km aprox


def test_haversine_simetrica():
    """La distancia debe ser simétrica: d(A,B) == d(B,A)."""
    d1 = database.haversine(37.389, -5.984, 37.412, -5.982)
    d2 = database.haversine(37.412, -5.982, 37.389, -5.984)
    assert abs(d1 - d2) < 0.01


# ── Cache de tiempos ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tiempos_cache_save_and_get(db_ready):
    """Guardar y recuperar tiempos cacheados."""
    tiempos = {"parada": "43", "tiempos": [{"linea": "01", "tiempo_minutos": 5}]}
    await database.save_tiempos_cache("43", tiempos)
    cached = await database.get_cached_tiempos("43")
    assert cached is not None
    assert cached["parada"] == "43"
    assert cached["tiempos"][0]["tiempo_minutos"] == 5


@pytest.mark.asyncio
async def test_tiempos_cache_miss(db_ready):
    """Cache miss debe devolver None."""
    cached = await database.get_cached_tiempos("NOEXISTE")
    assert cached is None


@pytest.mark.asyncio
async def test_tiempos_cache_expired(db_ready):
    """Tiempos expirados (>1 min) no deben devolverse."""
    import aiosqlite

    tiempos = {"parada": "43", "tiempos": []}
    expired_time = datetime.now() - timedelta(minutes=5)

    async with aiosqlite.connect(database.DATABASE_URL) as conn:
        await conn.execute(
            "INSERT INTO tiempos_cache (parada_codigo, tiempos_json, cached_at) VALUES (?, ?, ?)",
            ("43", json.dumps(tiempos), expired_time),
        )
        await conn.commit()

    cached = await database.get_cached_tiempos("43")
    assert cached is None


# ── Cache de direcciones ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_direccion_cache_save_and_get(db_ready):
    """Guardar y recuperar dirección cacheada."""
    direccion = {"calle": "Calle Test", "numero": "1", "municipio": "Sevilla"}
    await database.save_direccion_cache(37.389, -5.984, direccion)
    cached = await database.get_cached_direccion(37.389, -5.984)
    assert cached is not None
    assert cached["calle"] == "Calle Test"


@pytest.mark.asyncio
async def test_direccion_cache_miss(db_ready):
    """Cache miss de dirección debe devolver None."""
    cached = await database.get_cached_direccion(0.0, 0.0)
    assert cached is None
