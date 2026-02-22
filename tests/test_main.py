"""
Tests para app/main.py - Endpoints de la API.

Usa FastAPI TestClient con httpx para tests síncronos de endpoints.
Mockea el servicio de TUSSAM para no depender de la API externa.
"""

import pytest
import httpx
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app import database


# ── Helpers ──────────────────────────────────────────────────────────

def _make_client():
    """Crea un TestClient fresco sin ejecutar el lifespan."""
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


# ── GET / ────────────────────────────────────────────────────────────

def test_root():
    """GET / debe devolver info básica de la API."""
    client = _make_client()
    r = client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data["message"] == "TUSSAM API"
    assert "version" in data


# ── GET /health ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_ok(db_with_paradas):
    """Health check con DB accesible."""
    client = _make_client()
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["paradas_en_db"] == 3


@pytest.mark.asyncio
async def test_health_db_unavailable(db_ready, monkeypatch):
    """Health check con DB rota debe devolver 503."""
    async def _broken():
        raise RuntimeError("DB corrupta")

    monkeypatch.setattr(database, "get_all_paradas_from_db", _broken)
    client = _make_client()
    r = client.get("/health")
    assert r.status_code == 503


# ── GET /paradas ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_all_paradas(db_with_paradas):
    """Debe devolver todas las paradas."""
    client = _make_client()
    with patch("app.main.tussam_service.get_all_paradas", new_callable=AsyncMock) as mock:
        mock.return_value = [
            {"codigo": "43", "nombre": "Recaredo"},
            {"codigo": "44", "nombre": "San Esteban"},
        ]
        r = client.get("/paradas")
    assert r.status_code == 200
    assert len(r.json()) == 2


# ── GET /paradas/{codigo} ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_parada_existente(db_with_paradas):
    """Obtener parada por código."""
    client = _make_client()
    with patch("app.main.tussam_service.get_parada_by_codigo", new_callable=AsyncMock) as mock:
        mock.return_value = {"codigo": "43", "nombre": "Recaredo"}
        r = client.get("/paradas/43")
    assert r.status_code == 200
    assert r.json()["codigo"] == "43"


@pytest.mark.asyncio
async def test_get_parada_no_existe(db_ready):
    """Parada inexistente debe devolver 404."""
    client = _make_client()
    with patch("app.main.tussam_service.get_parada_by_codigo", new_callable=AsyncMock) as mock:
        mock.return_value = None
        r = client.get("/paradas/NOEXISTE")
    assert r.status_code == 404


# ── GET /paradas/{codigo}/tiempos ────────────────────────────────────

@pytest.mark.asyncio
async def test_get_tiempos(db_ready):
    """Tiempos de una parada."""
    client = _make_client()
    mock_tiempos = {
        "parada": "43",
        "tiempos": [{"linea": "01", "tiempo_minutos": 4, "sentido": 2}],
    }
    with patch("app.main.tussam_service.get_tiempos_parada", new_callable=AsyncMock) as mock:
        mock.return_value = mock_tiempos
        r = client.get("/paradas/43/tiempos")
    assert r.status_code == 200
    assert r.json()["tiempos"][0]["linea"] == "01"


@pytest.mark.asyncio
async def test_get_tiempos_api_caida(db_ready):
    """Si TUSSAM API falla con error HTTP, devolver 503."""
    client = _make_client()
    with patch("app.main.tussam_service.get_tiempos_parada", new_callable=AsyncMock) as mock:
        mock.side_effect = httpx.ConnectError("TUSSAM API down")
        r = client.get("/paradas/43/tiempos")
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_get_tiempos_error_inesperado(db_ready):
    """Si hay un error inesperado (no HTTP), devolver 500."""
    client = _make_client()
    with patch("app.main.tussam_service.get_tiempos_parada", new_callable=AsyncMock) as mock:
        mock.side_effect = ValueError("Bug en parsing")
        r = client.get("/paradas/43/tiempos")
    assert r.status_code == 500


# ── GET /paradas/{codigo}/lineas ─────────────────────────────────────

@pytest.mark.asyncio
async def test_get_lineas_de_parada(db_ready):
    """Líneas que pasan por una parada."""
    client = _make_client()
    with patch("app.main.tussam_service.get_lineas_de_parada", new_callable=AsyncMock) as mock:
        mock.return_value = ["01", "C4"]
        r = client.get("/paradas/43/lineas")
    assert r.status_code == 200
    assert r.json() == ["01", "C4"]


# ── GET /lineas ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_lineas(db_ready):
    """Todas las líneas."""
    client = _make_client()
    with patch("app.main.tussam_service.get_lineas", new_callable=AsyncMock) as mock:
        mock.return_value = [{"numero": "01", "nombre": "Test", "color": "#f00"}]
        r = client.get("/lineas")
    assert r.status_code == 200
    assert len(r.json()) == 1


# ── GET /lineas/{numero}/paradas ─────────────────────────────────────

@pytest.mark.asyncio
async def test_get_paradas_de_linea(db_ready):
    """Paradas de una línea."""
    client = _make_client()
    with patch("app.main.tussam_service.get_paradas_de_linea", new_callable=AsyncMock) as mock:
        mock.return_value = [{"codigo": "43", "sentido": 1, "orden": 0}]
        r = client.get("/lineas/01/paradas")
    assert r.status_code == 200
    assert r.json()[0]["codigo"] == "43"


# ── GET /paradas/cercanas ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_paradas_cercanas(db_ready):
    """Paradas cercanas sin tiempos."""
    client = _make_client()
    with patch("app.main.tussam_service.get_paradas_cercanas", new_callable=AsyncMock) as mock:
        mock.return_value = [
            {"codigo": "43", "nombre": "Recaredo", "distancia": 50,
             "latitud": 37.389, "longitud": -5.984}
        ]
        r = client.get("/paradas/cercanas?lat=37.389&lon=-5.984")
    assert r.status_code == 200
    assert len(r.json()) == 1


@pytest.mark.asyncio
async def test_paradas_cercanas_lat_invalida(db_ready):
    """Latitud fuera de rango debe devolver 400."""
    client = _make_client()
    r = client.get("/paradas/cercanas?lat=100&lon=-5.98")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_paradas_cercanas_lon_invalida(db_ready):
    """Longitud fuera de rango debe devolver 400."""
    client = _make_client()
    r = client.get("/paradas/cercanas?lat=37.38&lon=-200")
    assert r.status_code == 400


# ── GET /cercanas (endpoint principal) ───────────────────────────────

@pytest.mark.asyncio
async def test_cercanas_con_tiempos(db_ready):
    """Endpoint principal: paradas cercanas + tiempos."""
    client = _make_client()
    mock_paradas = [
        {"codigo": "43", "nombre": "Recaredo", "distancia": 50,
         "latitud": 37.389, "longitud": -5.984, "calle": "Calle Recaredo",
         "direccion_completa": "Calle Recaredo 5"}
    ]
    mock_tiempos = {
        "tiempos": [
            {"linea": "01", "color": "#f00", "tiempo_minutos": 4,
             "destino": "NORTE", "distancia_metros": 800, "sentido": 1}
        ]
    }
    with patch("app.main.tussam_service.get_paradas_cercanas", new_callable=AsyncMock) as mp, \
         patch("app.main.tussam_service.get_tiempos_parada", new_callable=AsyncMock) as mt:
        mp.return_value = mock_paradas
        mt.return_value = mock_tiempos
        r = client.get("/cercanas?lat=37.389&lon=-5.984")

    assert r.status_code == 200
    data = r.json()
    assert "paradas" in data
    assert "ubicacion" in data
    assert len(data["paradas"]) == 1
    assert data["paradas"][0]["tiempos"][0]["linea"] == "01"


@pytest.mark.asyncio
async def test_cercanas_filtro_tiempo_max(db_ready):
    """Filtrar por tiempo_max elimina buses lejanos."""
    client = _make_client()
    mock_paradas = [
        {"codigo": "43", "nombre": "Test", "distancia": 50,
         "latitud": 37.389, "longitud": -5.984, "calle": "", "direccion_completa": ""}
    ]
    mock_tiempos = {
        "tiempos": [
            {"linea": "01", "color": "#f00", "tiempo_minutos": 3,
             "destino": "A", "distancia_metros": 500, "sentido": 1},
            {"linea": "C4", "color": "#0f0", "tiempo_minutos": 15,
             "destino": "B", "distancia_metros": 3000, "sentido": 2},
        ]
    }
    with patch("app.main.tussam_service.get_paradas_cercanas", new_callable=AsyncMock) as mp, \
         patch("app.main.tussam_service.get_tiempos_parada", new_callable=AsyncMock) as mt:
        mp.return_value = mock_paradas
        mt.return_value = mock_tiempos
        r = client.get("/cercanas?lat=37.389&lon=-5.984&tiempo_max=10")

    data = r.json()
    tiempos = data["paradas"][0]["tiempos"]
    assert len(tiempos) == 1
    assert tiempos[0]["linea"] == "01"


@pytest.mark.asyncio
async def test_cercanas_filtro_lineas(db_ready):
    """Filtrar por líneas específicas."""
    client = _make_client()
    mock_paradas = [
        {"codigo": "43", "nombre": "Test", "distancia": 50,
         "latitud": 37.389, "longitud": -5.984, "calle": "", "direccion_completa": ""}
    ]
    mock_tiempos = {
        "tiempos": [
            {"linea": "01", "color": "#f00", "tiempo_minutos": 3,
             "destino": "A", "distancia_metros": 500, "sentido": 1},
            {"linea": "C4", "color": "#0f0", "tiempo_minutos": 5,
             "destino": "B", "distancia_metros": 800, "sentido": 2},
        ]
    }
    with patch("app.main.tussam_service.get_paradas_cercanas", new_callable=AsyncMock) as mp, \
         patch("app.main.tussam_service.get_tiempos_parada", new_callable=AsyncMock) as mt:
        mp.return_value = mock_paradas
        mt.return_value = mock_tiempos
        r = client.get("/cercanas?lat=37.389&lon=-5.984&lineas=C4")

    tiempos = r.json()["paradas"][0]["tiempos"]
    assert len(tiempos) == 1
    assert tiempos[0]["linea"] == "C4"


@pytest.mark.asyncio
async def test_cercanas_filtro_sentido(db_ready):
    """Filtrar por sentido."""
    client = _make_client()
    mock_paradas = [
        {"codigo": "43", "nombre": "Test", "distancia": 50,
         "latitud": 37.389, "longitud": -5.984, "calle": "", "direccion_completa": ""}
    ]
    mock_tiempos = {
        "tiempos": [
            {"linea": "01", "color": "#f00", "tiempo_minutos": 3,
             "destino": "A", "distancia_metros": 500, "sentido": 1},
            {"linea": "01", "color": "#f00", "tiempo_minutos": 8,
             "destino": "B", "distancia_metros": 1500, "sentido": 2},
        ]
    }
    with patch("app.main.tussam_service.get_paradas_cercanas", new_callable=AsyncMock) as mp, \
         patch("app.main.tussam_service.get_tiempos_parada", new_callable=AsyncMock) as mt:
        mp.return_value = mock_paradas
        mt.return_value = mock_tiempos
        r = client.get("/cercanas?lat=37.389&lon=-5.984&sentido=1")

    tiempos = r.json()["paradas"][0]["tiempos"]
    assert len(tiempos) == 1
    assert tiempos[0]["sentido"] == 1


@pytest.mark.asyncio
async def test_cercanas_formato_geojson(db_ready):
    """Formato GeoJSON."""
    client = _make_client()
    mock_paradas = [
        {"codigo": "43", "nombre": "Test", "distancia": 50,
         "latitud": 37.389, "longitud": -5.984, "calle": "", "direccion_completa": ""}
    ]
    with patch("app.main.tussam_service.get_paradas_cercanas", new_callable=AsyncMock) as mp, \
         patch("app.main.tussam_service.get_tiempos_parada", new_callable=AsyncMock) as mt:
        mp.return_value = mock_paradas
        mt.return_value = {"tiempos": []}
        r = client.get("/cercanas?lat=37.389&lon=-5.984&formato=geojson")

    data = r.json()
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == 1
    assert data["features"][0]["type"] == "Feature"
    assert data["features"][0]["geometry"]["type"] == "Point"


@pytest.mark.asyncio
async def test_cercanas_formato_invalido(db_ready):
    """Formato no soportado debe devolver 400."""
    client = _make_client()
    r = client.get("/cercanas?lat=37.389&lon=-5.984&formato=xml")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_cercanas_sentido_invalido(db_ready):
    """Sentido != 1 o 2 debe devolver 400."""
    client = _make_client()
    r = client.get("/cercanas?lat=37.389&lon=-5.984&sentido=3")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_cercanas_bearing_invalido(db_ready):
    """Bearing fuera de 0-360 debe devolver 400."""
    client = _make_client()
    r = client.get("/cercanas?lat=37.389&lon=-5.984&bearing=400")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_cercanas_incluir_mapa(db_ready):
    """incluir_mapa=true debe añadir URL de OpenStreetMap."""
    client = _make_client()
    mock_paradas = [
        {"codigo": "43", "nombre": "Test", "distancia": 50,
         "latitud": 37.389, "longitud": -5.984, "calle": "", "direccion_completa": ""}
    ]
    with patch("app.main.tussam_service.get_paradas_cercanas", new_callable=AsyncMock) as mp, \
         patch("app.main.tussam_service.get_tiempos_parada", new_callable=AsyncMock) as mt:
        mp.return_value = mock_paradas
        mt.return_value = {"tiempos": []}
        r = client.get("/cercanas?lat=37.389&lon=-5.984&incluir_mapa=true")

    parada = r.json()["paradas"][0]
    assert "mapa_url" in parada
    assert "openstreetmap.org" in parada["mapa_url"]


@pytest.mark.asyncio
async def test_cercanas_tussam_api_error_graceful(db_ready):
    """Si TUSSAM API falla para una parada, sigue con tiempos vacíos."""
    client = _make_client()
    mock_paradas = [
        {"codigo": "43", "nombre": "Test", "distancia": 50,
         "latitud": 37.389, "longitud": -5.984, "calle": "", "direccion_completa": ""}
    ]
    with patch("app.main.tussam_service.get_paradas_cercanas", new_callable=AsyncMock) as mp, \
         patch("app.main.tussam_service.get_tiempos_parada", new_callable=AsyncMock) as mt:
        mp.return_value = mock_paradas
        mt.side_effect = Exception("TUSSAM down")
        r = client.get("/cercanas?lat=37.389&lon=-5.984")

    assert r.status_code == 200
    assert r.json()["paradas"][0]["tiempos"] == []


# ── Validación de parámetros ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_radio_minimo(db_ready):
    """radio < 50 debe dar 422 (validación FastAPI ge=50)."""
    client = _make_client()
    r = client.get("/cercanas?lat=37.389&lon=-5.984&radio=10")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_radio_maximo(db_ready):
    """radio > 2000 debe dar 422 (validación FastAPI le=2000)."""
    client = _make_client()
    r = client.get("/cercanas?lat=37.389&lon=-5.984&radio=5000")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_max_paradas_minimo(db_ready):
    """max_paradas < 1 debe dar 422."""
    client = _make_client()
    r = client.get("/cercanas?lat=37.389&lon=-5.984&max_paradas=0")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_max_paradas_maximo(db_ready):
    """max_paradas > 10 debe dar 422."""
    client = _make_client()
    r = client.get("/cercanas?lat=37.389&lon=-5.984&max_paradas=50")
    assert r.status_code == 422


# ── POST /sync/* (autenticación) ─────────────────────────────────────

@pytest.mark.asyncio
async def test_sync_sin_key_con_env(db_ready, monkeypatch):
    """Sync sin API key cuando SYNC_API_KEY está configurada debe dar 403."""
    monkeypatch.setenv("SYNC_API_KEY", "secret-key-123")
    client = _make_client()
    with patch("app.main.tussam_service.sync_paradas_from_api", new_callable=AsyncMock):
        r = client.post("/sync/paradas")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_sync_con_key_correcta(db_ready, monkeypatch):
    """Sync con API key correcta debe funcionar."""
    monkeypatch.setenv("SYNC_API_KEY", "secret-key-123")
    client = _make_client()
    with patch("app.main.tussam_service.sync_paradas_from_api", new_callable=AsyncMock) as mock:
        mock.return_value = 100
        r = client.post("/sync/paradas", headers={"X-API-Key": "secret-key-123"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_sync_con_key_incorrecta(db_ready, monkeypatch):
    """Sync con API key incorrecta debe dar 403."""
    monkeypatch.setenv("SYNC_API_KEY", "secret-key-123")
    client = _make_client()
    with patch("app.main.tussam_service.sync_paradas_from_api", new_callable=AsyncMock):
        r = client.post("/sync/paradas", headers={"X-API-Key": "wrong-key"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_sync_sin_env_key(db_ready, monkeypatch):
    """Sin SYNC_API_KEY configurada, sync debe quedar deshabilitado (fail-closed)."""
    monkeypatch.delenv("SYNC_API_KEY", raising=False)
    monkeypatch.delenv("ALLOW_INSECURE_SYNC", raising=False)
    client = _make_client()
    with patch("app.main.tussam_service.sync_paradas_from_api", new_callable=AsyncMock) as mock:
        mock.return_value = 100
        r = client.post("/sync/paradas")
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_sync_sin_env_key_permitido_en_modo_inseguro(db_ready, monkeypatch):
    """ALLOW_INSECURE_SYNC=true habilita sync sin key para desarrollo local."""
    monkeypatch.delenv("SYNC_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_INSECURE_SYNC", "true")
    client = _make_client()
    with patch("app.main.tussam_service.sync_paradas_from_api", new_callable=AsyncMock) as mock:
        mock.return_value = 100
        r = client.post("/sync/paradas")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_sync_rechaza_key_insegura_por_defecto(db_ready, monkeypatch):
    """Una API key por defecto/predecible debe bloquear endpoints de sync."""
    monkeypatch.setenv("SYNC_API_KEY", "cambia-esta-clave")
    client = _make_client()
    with patch("app.main.tussam_service.sync_paradas_from_api", new_callable=AsyncMock):
        r = client.post("/sync/paradas", headers={"X-API-Key": "cambia-esta-clave"})
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_sync_all(db_ready, monkeypatch):
    """POST /sync/all debe sincronizar todo."""
    monkeypatch.setenv("SYNC_API_KEY", "secret-key-123")
    client = _make_client()
    with patch("app.main.tussam_service.sync_paradas_from_api", new_callable=AsyncMock) as mp, \
         patch("app.main.tussam_service.sync_lineas_from_api", new_callable=AsyncMock) as ml, \
         patch("app.main.tussam_service.sync_paradas_lineas_from_api", new_callable=AsyncMock) as mr:
        mp.return_value = 967
        ml.return_value = 43
        mr.return_value = 1756
        r = client.post("/sync/all", headers={"X-API-Key": "secret-key-123"})

    assert r.status_code == 200
    data = r.json()
    assert data["paradas"] == 967
    assert data["lineas"] == 43
    assert data["paradas_lineas"] == 1756


# ── Rate Limiting ────────────────────────────────────────────────────

def test_rate_limit_device_header():
    """X-Device-ID debe activar rate limit por dispositivo."""
    client = _make_client()
    # Hacer muchas peticiones con el mismo Device-ID
    for _ in range(60):
        r = client.get("/", headers={"X-Device-ID": "test-device"})
        assert r.status_code == 200

    # La 61ª debe dar 429
    r = client.get("/", headers={"X-Device-ID": "test-device"})
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_rate_limit_different_devices():
    """Diferentes Device-IDs no deben compartir rate limit."""
    client = _make_client()
    for i in range(60):
        client.get("/", headers={"X-Device-ID": "device-A"})

    # device-B debe funcionar sin problemas
    r = client.get("/", headers={"X-Device-ID": "device-B"})
    assert r.status_code == 200
