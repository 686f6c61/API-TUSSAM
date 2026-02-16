"""
Fixtures compartidos para los tests de TUSSAM API.
"""

import os
import pytest
import pytest_asyncio
import aiosqlite

from app import database


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path, monkeypatch):
    """Usa una base de datos temporal para cada test."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(database, "DATABASE_URL", db_path)
    yield db_path


@pytest_asyncio.fixture
async def db_ready(_use_tmp_db):
    """Inicializa la DB temporal y devuelve la ruta."""
    await database.init_db()
    return _use_tmp_db


@pytest_asyncio.fixture
async def db_with_paradas(db_ready):
    """DB con paradas de ejemplo precargadas."""
    paradas = [
        {"codigo": "43", "nombre": "Recaredo (Puerta Carmona)",
         "latitud": 37.389663, "longitud": -5.984265, "calle": "Calle Recaredo", "numero": "5"},
        {"codigo": "44", "nombre": "San Esteban",
         "latitud": 37.388500, "longitud": -5.985000, "calle": "Calle San Esteban", "numero": "12"},
        {"codigo": "252", "nombre": "Trabaj. Inmigrantes",
         "latitud": 37.412338, "longitud": -5.982419, "calle": None, "numero": None},
    ]
    await database.save_paradas_batch(paradas)
    return paradas


@pytest_asyncio.fixture
async def db_with_lineas(db_ready):
    """DB con líneas de ejemplo precargadas."""
    lineas = [
        {"numero": "01", "nombre": "Plg. Norte - H. Virgen del Rocio", "color": "#f54129"},
        {"numero": "C4", "nombre": "Circular 4", "color": "#008431"},
    ]
    await database.save_lineas_batch(lineas)
    return lineas


@pytest_asyncio.fixture
async def db_with_relations(db_with_paradas, db_with_lineas):
    """DB con paradas, líneas y relaciones precargadas."""
    relaciones = [
        {"parada_codigo": "43", "linea_numero": "01", "sentido": 1, "orden": 5},
        {"parada_codigo": "43", "linea_numero": "01", "sentido": 2, "orden": 8},
        {"parada_codigo": "43", "linea_numero": "C4", "sentido": 1, "orden": 12},
        {"parada_codigo": "44", "linea_numero": "01", "sentido": 1, "orden": 6},
        {"parada_codigo": "252", "linea_numero": "01", "sentido": 1, "orden": 0},
    ]
    await database.save_paradas_lineas_batch(relaciones)
    return relaciones
