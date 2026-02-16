"""
Tests End-to-End con datos reales de la API de TUSSAM.

Estos tests golpean la API real de TUSSAM (reddelineas.tussam.es).
Se ejecutan secuencialmente con delays entre peticiones para evitar rate limiting (429).

Ejecutar:
    python -m pytest tests/test_e2e.py -v -s

NOTA: Requiere conexión a internet. Si la API de TUSSAM está caída, los tests fallarán.
"""

import time
import pytest
import asyncio
import httpx
from fastapi.testclient import TestClient

from app import database
from app.services.tussam import TussamService, tussam_service


# ── 10 paradas reales de Sevilla (línea 01 sentido 1) ────────────────
PARADAS_REALES = [
    {"codigo": "252", "nombre": "Trabaj. Inmigrantes (Diego de Almagro)",
     "latitud": 37.412338, "longitud": -5.982419},
    {"codigo": "218", "nombre": "Trabaj. Inmigrantes (Diego Puerta)",
     "latitud": 37.411037, "longitud": -5.980907},
    {"codigo": "253", "nombre": "Trabaj. Inmigrantes (Los Romeros)",
     "latitud": 37.408466, "longitud": -5.979802},
    {"codigo": "111", "nombre": "Sor Francisca Dorotea",
     "latitud": 37.407341, "longitud": -5.981036},
    {"codigo": "275", "nombre": "Dr. Leal Castaños (Avda. San Lázaro)",
     "latitud": 37.407348, "longitud": -5.983696},
    {"codigo": "112", "nombre": "San Juan de Ribera (Policlínico)",
     "latitud": 37.405532, "longitud": -5.986733},
    {"codigo": "246", "nombre": "Muñoz León (Puerta de Córdoba)",
     "latitud": 37.402549, "longitud": -5.987065},
    {"codigo": "20", "nombre": "Ronda de Capuchinos (San Julián)",
     "latitud": 37.399726, "longitud": -5.983828},
    {"codigo": "889", "nombre": "Recaredo (San Roque)",
     "latitud": 37.391250, "longitud": -5.984236},
    {"codigo": "2", "nombre": "Menéndez Pelayo (Puerta Carmona)",
     "latitud": 37.388370, "longitud": -5.984992},
]

# Delay entre peticiones a TUSSAM (segundos)
DELAY = 3


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _use_tmp_db(e2e_db_path):
    """Override del fixture de conftest.py para usar la DB compartida del módulo E2E."""
    database.DATABASE_URL = e2e_db_path


@pytest.fixture(autouse=True)
def _fresh_http_client():
    """Recrea el httpx.AsyncClient del singleton para evitar 'Event loop is closed'.

    TestClient crea/destruye event loops entre invocaciones sync.
    El AsyncClient retiene conexiones del loop anterior, causando RuntimeError.
    Recrear el cliente garantiza que siempre se vincule al loop actual.
    """
    tussam_service.client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)


@pytest.fixture(scope="module")
def e2e_db_path(tmp_path_factory):
    """Crea una DB temporal, la inicializa y carga datos para todo el módulo E2E."""
    db_path = str(tmp_path_factory.mktemp("e2e") / "tussam_e2e.db")
    database.DATABASE_URL = db_path

    loop = asyncio.new_event_loop()
    loop.run_until_complete(database.init_db())
    loop.run_until_complete(database.save_paradas_batch(PARADAS_REALES))

    # Insertar la línea 01 ANTES de las relaciones
    loop.run_until_complete(
        database.save_lineas_batch([
            {"numero": "01", "nombre": "Plg. Norte - H. Virgen del Rocio", "color": "#f54129"}
        ])
    )

    # Insertar relaciones parada-línea
    relaciones = [
        {"parada_codigo": p["codigo"], "linea_numero": "01",
         "sentido": 1, "orden": i}
        for i, p in enumerate(PARADAS_REALES)
    ]
    loop.run_until_complete(database.save_paradas_lineas_batch(relaciones))
    loop.close()

    yield db_path


@pytest.fixture(scope="module")
def client(e2e_db_path):
    """TestClient de FastAPI con DB real precargada."""
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def service():
    """Servicio TUSSAM con cliente HTTP real."""
    return TussamService()


# ══════════════════════════════════════════════════════════════════════
# FASE 1: Verificar datos cargados en DB
# ══════════════════════════════════════════════════════════════════════

class TestFase1_DatosLocales:
    """Verifica que los datos locales están correctos antes de golpear la API."""

    def test_paradas_cargadas(self, client):
        """Las 10 paradas deben estar en la DB."""
        r = client.get("/paradas")
        assert r.status_code == 200
        paradas = r.json()
        assert len(paradas) == 10
        codigos = {p["codigo"] for p in paradas}
        assert codigos == {p["codigo"] for p in PARADAS_REALES}

    def test_parada_individual(self, client):
        """Obtener una parada por código."""
        r = client.get("/paradas/252")
        assert r.status_code == 200
        data = r.json()
        assert data["nombre"] == "Trabaj. Inmigrantes (Diego de Almagro)"
        assert abs(data["latitud"] - 37.412338) < 0.001

    def test_lineas_cargadas(self, client):
        """La línea 01 debe estar en la DB."""
        r = client.get("/lineas")
        assert r.status_code == 200
        lineas = r.json()
        assert len(lineas) == 1
        assert lineas[0]["numero"] == "01"

    def test_lineas_de_parada(self, client):
        """Las 10 paradas deben tener la línea 01."""
        r = client.get("/paradas/252/lineas")
        assert r.status_code == 200
        assert "01" in r.json()

    def test_paradas_de_linea(self, client):
        """La línea 01 debe tener las 10 paradas."""
        r = client.get("/lineas/01/paradas")
        assert r.status_code == 200
        paradas = r.json()
        assert len(paradas) == 10

    def test_health(self, client):
        """Health check con datos reales."""
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["paradas_en_db"] == 10


# ══════════════════════════════════════════════════════════════════════
# FASE 2: Tiempos reales - una parada a la vez
# ══════════════════════════════════════════════════════════════════════

class TestFase2_TiemposReales:
    """
    Obtiene tiempos de llegada REALES de TUSSAM para cada parada.
    Una petición a la vez con delay entre cada una.
    """

    @pytest.mark.parametrize("parada", PARADAS_REALES, ids=[p["codigo"] for p in PARADAS_REALES])
    def test_tiempos_parada_real(self, client, parada):
        """
        GET /paradas/{codigo}/tiempos contra la API real de TUSSAM.
        Verifica estructura de la respuesta.
        """
        codigo = parada["codigo"]
        print(f"\n  → Pidiendo tiempos para parada {codigo} ({parada['nombre']})...")

        r = client.get(f"/paradas/{codigo}/tiempos")

        # Puede ser 200 (OK) o 503 (TUSSAM caída) - ambos son válidos en E2E
        assert r.status_code in (200, 503), f"Status inesperado: {r.status_code}"

        if r.status_code == 200:
            data = r.json()

            # Estructura obligatoria
            assert "parada" in data
            assert "tiempos" in data
            assert data["parada"] == codigo

            # Validar cada tiempo
            for t in data["tiempos"]:
                assert "linea" in t, f"Falta 'linea' en tiempo: {t}"
                assert "tiempo_minutos" in t, f"Falta 'tiempo_minutos' en tiempo: {t}"
                assert "destino" in t, f"Falta 'destino' en tiempo: {t}"
                assert "sentido" in t, f"Falta 'sentido' en tiempo: {t}"
                assert isinstance(t["tiempo_minutos"], int)
                # TUSSAM puede devolver valores negativos (bus ya en la parada)
                assert t["tiempo_minutos"] >= -60, \
                    f"tiempo_minutos={t['tiempo_minutos']} demasiado negativo"
                assert isinstance(t["linea"], str)
                assert len(t["linea"]) > 0
                # sentido puede ser 1, 2 o None
                assert t["sentido"] in (1, 2, None), f"Sentido inválido: {t['sentido']}"

            n_tiempos = len(data["tiempos"])
            print(f"    ✓ Parada {codigo}: {n_tiempos} buses en camino")
            for t in data["tiempos"][:3]:
                print(f"      Línea {t['linea']} → {t['destino']} en {t['tiempo_minutos']} min")
        else:
            print(f"    ⚠ Parada {codigo}: TUSSAM API no disponible (503)")

        # Delay antes de la siguiente petición
        time.sleep(DELAY)


# ══════════════════════════════════════════════════════════════════════
# FASE 3: Endpoint /cercanas con coordenadas reales
# ══════════════════════════════════════════════════════════════════════

class TestFase3_CercanasReal:
    """
    Prueba el endpoint principal /cercanas con coordenadas reales de Sevilla.
    Cada test espera entre peticiones.
    """

    def test_cercanas_centro_sevilla(self, client):
        """Paradas cercanas al centro de Sevilla (Puerta de Córdoba)."""
        # Coordenadas cerca de parada 246 (Muñoz León / Puerta de Córdoba)
        r = client.get("/cercanas?lat=37.4025&lon=-5.9870&radio=300&max_paradas=2")
        assert r.status_code == 200
        data = r.json()

        assert "ubicacion" in data
        assert "paradas" in data
        assert data["ubicacion"]["lat"] == 37.4025

        paradas = data["paradas"]
        print(f"\n  → /cercanas centro: {len(paradas)} parada(s) encontrada(s)")

        for p in paradas:
            # Estructura obligatoria
            assert "codigo" in p
            assert "nombre" in p
            assert "distancia" in p
            assert "tiempos" in p
            assert p["distancia"] <= 300

            print(f"    {p['nombre']} ({p['distancia']}m) - {len(p['tiempos'])} buses")

        time.sleep(DELAY)

    def test_cercanas_con_bearing(self, client):
        """Cercanas filtrando por orientación (mirando al sur)."""
        r = client.get(
            "/cercanas?lat=37.400&lon=-5.984&radio=500&max_paradas=3&bearing=180"
        )
        assert r.status_code == 200
        data = r.json()

        assert data["ubicacion"]["bearing"] == 180

        for p in data["paradas"]:
            # Con bearing, debe incluir bearing_diff
            if p.get("bearing_diff") is not None:
                assert isinstance(p["bearing_diff"], (int, float))

        print(f"\n  → /cercanas con bearing=180: {len(data['paradas'])} parada(s)")
        time.sleep(DELAY)

    def test_cercanas_filtro_linea(self, client):
        """Filtrar solo línea 01."""
        r = client.get(
            "/cercanas?lat=37.400&lon=-5.984&radio=500&max_paradas=3&lineas=01"
        )
        assert r.status_code == 200
        data = r.json()

        for p in data["paradas"]:
            for t in p["tiempos"]:
                assert t["linea"] == "01", f"Línea {t['linea']} no debería estar (filtro: 01)"

        print(f"\n  → /cercanas filtro linea=01: {len(data['paradas'])} parada(s)")
        time.sleep(DELAY)

    def test_cercanas_filtro_sentido(self, client):
        """Filtrar solo sentido 1."""
        r = client.get(
            "/cercanas?lat=37.400&lon=-5.984&radio=500&max_paradas=2&sentido=1"
        )
        assert r.status_code == 200
        data = r.json()

        for p in data["paradas"]:
            for t in p["tiempos"]:
                assert t.get("sentido") in (1, None), \
                    f"Sentido {t['sentido']} no debería pasar el filtro"

        print(f"\n  → /cercanas filtro sentido=1: {len(data['paradas'])} parada(s)")
        time.sleep(DELAY)

    def test_cercanas_formato_geojson(self, client):
        """Respuesta en formato GeoJSON."""
        r = client.get(
            "/cercanas?lat=37.400&lon=-5.984&radio=500&max_paradas=1&formato=geojson"
        )
        assert r.status_code == 200
        data = r.json()

        assert data["type"] == "FeatureCollection"
        assert "features" in data

        if data["features"]:
            feature = data["features"][0]
            assert feature["type"] == "Feature"
            assert feature["geometry"]["type"] == "Point"
            coords = feature["geometry"]["coordinates"]
            assert len(coords) == 2
            # GeoJSON: [lon, lat]
            assert -6.5 < coords[0] < -5.5, f"Longitud fuera de Sevilla: {coords[0]}"
            assert 37.0 < coords[1] < 38.0, f"Latitud fuera de Sevilla: {coords[1]}"

        print(f"\n  → /cercanas GeoJSON: {len(data['features'])} feature(s)")
        time.sleep(DELAY)

    def test_cercanas_incluir_mapa(self, client):
        """Con incluir_mapa=true, debe añadir URL de OpenStreetMap."""
        r = client.get(
            "/cercanas?lat=37.400&lon=-5.984&radio=500&max_paradas=1&incluir_mapa=true"
        )
        assert r.status_code == 200
        data = r.json()

        for p in data["paradas"]:
            assert "mapa_url" in p
            assert "openstreetmap.org" in p["mapa_url"]
            assert str(p["latitud"]) in p["mapa_url"]

        print(f"\n  → /cercanas con mapa: URL generada OK")
        time.sleep(DELAY)

    def test_cercanas_tiempo_max(self, client):
        """Filtrar buses que llegan en máximo 10 minutos."""
        r = client.get(
            "/cercanas?lat=37.400&lon=-5.984&radio=500&max_paradas=2&tiempo_max=10"
        )
        assert r.status_code == 200
        data = r.json()

        for p in data["paradas"]:
            for t in p["tiempos"]:
                assert t["tiempo_minutos"] <= 10, \
                    f"Bus a {t['tiempo_minutos']} min excede tiempo_max=10"

        n = sum(len(p["tiempos"]) for p in data["paradas"])
        print(f"\n  → /cercanas tiempo_max=10: {n} buses en ≤10 min")
        time.sleep(DELAY)


# ══════════════════════════════════════════════════════════════════════
# FASE 4: Endpoint /paradas/cercanas (sin tiempos)
# ══════════════════════════════════════════════════════════════════════

class TestFase4_ParadasCercanas:
    """Prueba /paradas/cercanas que devuelve paradas sin tiempos (más rápido)."""

    def test_paradas_cercanas_basico(self, client):
        """Paradas cercanas sin tiempos."""
        r = client.get("/paradas/cercanas?lat=37.400&lon=-5.984&radio=1000")
        assert r.status_code == 200
        paradas = r.json()

        assert len(paradas) > 0
        for p in paradas:
            assert "codigo" in p
            assert "nombre" in p
            assert "distancia" in p
            assert p["distancia"] <= 1000
            # No debe tener tiempos (es /paradas/cercanas, no /cercanas)
            assert "tiempos" not in p

        print(f"\n  → /paradas/cercanas: {len(paradas)} parada(s) en 1km")

    def test_paradas_cercanas_radio_pequeño(self, client):
        """Radio muy pequeño cerca de una parada conocida."""
        # Muy cerca de parada 20 (37.399726, -5.983828)
        r = client.get("/paradas/cercanas?lat=37.3997&lon=-5.9838&radio=50")
        assert r.status_code == 200
        paradas = r.json()

        if paradas:
            assert paradas[0]["distancia"] <= 50
            print(f"\n  → /paradas/cercanas radio=50m: {paradas[0]['nombre']}")
        else:
            print("\n  → /paradas/cercanas radio=50m: ninguna (OK)")


# ══════════════════════════════════════════════════════════════════════
# FASE 5: Validaciones de error
# ══════════════════════════════════════════════════════════════════════

class TestFase5_Validaciones:
    """Verifica que las validaciones funcionan con la app real."""

    def test_parada_inexistente(self, client):
        """Parada que no existe debe dar 404."""
        r = client.get("/paradas/99999")
        assert r.status_code == 404

    def test_lat_invalida(self, client):
        """Latitud fuera de rango."""
        r = client.get("/cercanas?lat=100&lon=-5.98")
        assert r.status_code == 400

    def test_radio_excesivo(self, client):
        """Radio > 2000 debe dar 422."""
        r = client.get("/cercanas?lat=37.39&lon=-5.98&radio=5000")
        assert r.status_code == 422

    def test_formato_invalido(self, client):
        """Formato no soportado."""
        r = client.get("/cercanas?lat=37.39&lon=-5.98&formato=xml")
        assert r.status_code == 400

    def test_sentido_invalido(self, client):
        """Sentido != 1 o 2."""
        r = client.get("/cercanas?lat=37.39&lon=-5.98&sentido=3")
        assert r.status_code == 400
