"""
Tests para app/services/tussam.py - Servicio de TUSSAM.

Mockea las peticiones HTTP para no depender de la API externa.
"""

import pytest
import math
from unittest.mock import AsyncMock, patch, MagicMock

from app import database
from app.services.tussam import TussamService


@pytest.fixture
def service():
    """Crea una instancia limpia del servicio."""
    return TussamService()


# ── Utilidades internas ──────────────────────────────────────────────

def test_format_datetime(service):
    """_format_datetime debe producir formato correcto para TUSSAM."""
    from datetime import datetime
    result = service._format_datetime(datetime(2026, 2, 16, 10, 30, 0))
    assert result == "16-02-2026T10%3A30%3A00"


def test_coords_to_tussam(service):
    """Coordenadas deben multiplicarse por 10^6."""
    lat, lon = service._coords_to_tussam(37.389663, -5.984265)
    assert lat == 37389663
    assert lon == -5984265


def test_calculate_bearing_north(service):
    """Bearing hacia el norte debe ser ~0°."""
    bearing = service._calculate_bearing(37.0, -6.0, 38.0, -6.0)
    assert bearing < 1 or bearing > 359  # ~0°


def test_calculate_bearing_east(service):
    """Bearing hacia el este debe ser ~90°."""
    bearing = service._calculate_bearing(37.0, -6.0, 37.0, -5.0)
    assert 85 < bearing < 95


def test_calculate_bearing_south(service):
    """Bearing hacia el sur debe ser ~180°."""
    bearing = service._calculate_bearing(38.0, -6.0, 37.0, -6.0)
    assert 175 < bearing < 185


def test_bearing_diff_normal(service):
    """Diferencia entre bearings normales."""
    assert service._bearing_diff(10, 30) == 20
    assert service._bearing_diff(30, 10) == 20


def test_bearing_diff_wrap_around(service):
    """Diferencia cruzando el 0° (Norte)."""
    diff = service._bearing_diff(350, 10)
    assert diff == 20


def test_bearing_diff_opposite(service):
    """Bearings opuestos = 180°."""
    assert service._bearing_diff(0, 180) == 180
    assert service._bearing_diff(90, 270) == 180


# ── get_paradas_cercanas ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_paradas_cercanas(service, db_with_paradas):
    """Debe devolver paradas dentro del radio, ordenadas por distancia."""
    # Punto muy cerca de parada 43 (37.389663, -5.984265)
    result = await service.get_paradas_cercanas(37.3897, -5.9843, radio=200)
    assert len(result) >= 1
    assert result[0]["codigo"] == "43"
    assert result[0]["distancia"] <= 200


@pytest.mark.asyncio
async def test_get_paradas_cercanas_radio_pequeño(service, db_with_paradas):
    """Radio muy pequeño no debe devolver nada si no hay paradas."""
    result = await service.get_paradas_cercanas(37.3, -5.9, radio=50)
    assert result == []


@pytest.mark.asyncio
async def test_get_paradas_cercanas_con_bearing(service, db_with_paradas):
    """Con bearing, debe añadir bearing_diff y ordenar por ese campo."""
    result = await service.get_paradas_cercanas(
        37.3897, -5.9843, radio=5000, bearing=180
    )
    assert len(result) > 0
    for p in result:
        assert "bearing" in p
        assert "bearing_diff" in p
    # Verificar que está ordenado por bearing_diff
    diffs = [p["bearing_diff"] for p in result]
    assert diffs == sorted(diffs)


@pytest.mark.asyncio
async def test_get_parada_by_codigo(service, db_with_paradas):
    """Obtener parada por código."""
    result = await service.get_parada_by_codigo("43")
    assert result["nombre"] == "Recaredo (Puerta Carmona)"


@pytest.mark.asyncio
async def test_get_parada_by_codigo_inexistente(service, db_ready):
    """Parada inexistente debe devolver None."""
    result = await service.get_parada_by_codigo("NOEXISTE")
    assert result is None


# ── get_tiempos_parada ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_tiempos_parada_cache_hit(service, db_ready):
    """Si hay cache válido, no debe hacer petición HTTP."""
    cached_data = {
        "parada": "43",
        "nombre": "Test",
        "tiempos": [{"linea": "01", "tiempo_minutos": 5}],
    }
    await database.save_tiempos_cache("43", cached_data)

    result = await service.get_tiempos_parada("43")
    assert result["tiempos"][0]["tiempo_minutos"] == 5


@pytest.mark.asyncio
async def test_get_tiempos_parada_fresh(service, db_ready):
    """Sin cache, debe hacer petición a TUSSAM y cachear resultado."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "result": {
            "descripcion": {"texto": "Recaredo"},
            "posicion": {"latitudE6": 37389663, "longitudE6": -5984265},
            "lineasCoincidentes": [
                {
                    "labelLinea": "01",
                    "color": "#f54129",
                    "estimaciones": [
                        {
                            "segundos": 240,
                            "destino": {"texto": "POLIGONO NORTE"},
                            "distancia": 783,
                        }
                    ],
                }
            ],
        }
    }
    mock_response.raise_for_status = MagicMock()

    with patch.object(service.client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        # Mockear get_sentidos_for_parada
        with patch.object(database, "get_sentidos_for_parada", new_callable=AsyncMock) as mock_sentidos:
            mock_sentidos.return_value = {"01": [2]}
            result = await service.get_tiempos_parada("43")

    assert result["parada"] == "43"
    assert result["nombre"] == "Recaredo"
    assert len(result["tiempos"]) == 1
    assert result["tiempos"][0]["linea"] == "01"
    assert result["tiempos"][0]["tiempo_minutos"] == 4
    assert result["tiempos"][0]["sentido"] == 2


@pytest.mark.asyncio
async def test_get_tiempos_sentido_ambiguo(service, db_ready):
    """Si una línea tiene ambos sentidos en la parada, sentido debe ser None."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "result": {
            "descripcion": {"texto": "Test"},
            "posicion": {},
            "lineasCoincidentes": [
                {
                    "labelLinea": "C4",
                    "color": "#008431",
                    "estimaciones": [
                        {"segundos": 300, "destino": {"texto": "DESTINO"}, "distancia": 500}
                    ],
                }
            ],
        }
    }
    mock_response.raise_for_status = MagicMock()

    with patch.object(service.client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        with patch.object(database, "get_sentidos_for_parada", new_callable=AsyncMock) as mock_sentidos:
            mock_sentidos.return_value = {"C4": [1, 2]}  # Ambos sentidos
            result = await service.get_tiempos_parada("43", force_refresh=True)

    assert result["tiempos"][0]["sentido"] is None


# ── _get_with_retry ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_with_retry_success(service):
    """Primera petición exitosa no reintenta."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()

    with patch.object(service.client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        result = await service._get_with_retry("http://test.com")
    assert result == mock_response
    assert mock_get.call_count == 1


@pytest.mark.asyncio
async def test_get_with_retry_429(service):
    """429 debe reintentar con backoff."""
    mock_429 = MagicMock()
    mock_429.status_code = 429

    mock_ok = MagicMock()
    mock_ok.status_code = 200
    mock_ok.raise_for_status = MagicMock()

    with patch.object(service.client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [mock_429, mock_ok]
        with patch("app.services.tussam.asyncio.sleep", new_callable=AsyncMock):
            result = await service._get_with_retry("http://test.com")
    assert result.status_code == 200
    assert mock_get.call_count == 2


# ── get_direccion_from_coords ────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_direccion_cache_hit(service, db_ready):
    """Si hay cache de dirección, no debe hacer petición HTTP."""
    cached = {"calle": "Calle Test", "numero": "1", "municipio": "Sevilla"}
    await database.save_direccion_cache(37.389, -5.984, cached)

    result = await service.get_direccion_from_coords(37.389, -5.984)
    assert result["calle"] == "Calle Test"


@pytest.mark.asyncio
async def test_get_direccion_nominatim(service, db_ready):
    """Sin cache, debe consultar Nominatim y cachear."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "address": {
            "road": "Calle Sierpes",
            "house_number": "10",
            "postcode": "41004",
            "city": "Sevilla",
            "county": "Sevilla",
        }
    }

    with patch.object(service.client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        result = await service.get_direccion_from_coords(37.39, -5.99)

    assert result["calle"] == "Calle Sierpes"
    assert result["numero"] == "10"
    assert result["direccion_completa"] == "Calle Sierpes 10"


@pytest.mark.asyncio
async def test_get_direccion_error(service, db_ready):
    """Si Nominatim falla, devuelve valores por defecto."""
    with patch.object(service.client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = Exception("Network error")
        result = await service.get_direccion_from_coords(37.39, -5.99)

    assert result["calle"] == ""
    assert result["municipio"] == "Sevilla"


# ── Líneas ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_lineas(service, db_with_lineas):
    """Obtener todas las líneas de la DB."""
    result = await service.get_lineas()
    assert len(result) == 2
    numeros = {l["numero"] for l in result}
    assert numeros == {"01", "C4"}


@pytest.mark.asyncio
async def test_get_lineas_de_parada(service, db_with_relations):
    """Obtener líneas que pasan por una parada."""
    result = await service.get_lineas_de_parada("43")
    assert set(result) == {"01", "C4"}


@pytest.mark.asyncio
async def test_get_paradas_de_linea(service, db_with_relations):
    """Obtener paradas de una línea."""
    result = await service.get_paradas_de_linea("01")
    assert len(result) >= 3
    codigos = {p["codigo"] for p in result}
    assert "43" in codigos
    assert "252" in codigos
