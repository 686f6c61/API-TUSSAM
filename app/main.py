"""
TUSSAM API - Aplicación Principal
================================

Puntos de entrada (endpoints) de la API.
Expone los servicios de TUSSAM a través de HTTP.

Autor: 686f6c61 (https://github.com/686f6c61)
Versión: 1.0.0
Licencia: MIT
"""

import os
import hmac
import time
import logging
import httpx
from collections import defaultdict

from fastapi import FastAPI, Query, HTTPException, Request, Depends, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from typing import List
from contextlib import asynccontextmanager
from app.services.tussam import tussam_service
from app import database
from app.scheduler import start_scheduler, stop_scheduler

logger = logging.getLogger("tussam.api")

# --- Autenticación para endpoints de sync ---
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_sync_key(api_key: str = Security(api_key_header)):
    """Verifica la API key para endpoints de sincronización."""
    expected = os.getenv("SYNC_API_KEY", "")
    if not expected:
        logger.warning("SYNC_API_KEY no configurada — endpoints de sync SIN PROTECCIÓN")
        return
    if not api_key or not hmac.compare_digest(api_key, expected):
        raise HTTPException(status_code=403, detail="API key inválida o ausente")


# --- Rate limiting ---
# Dos niveles: por dispositivo (X-Device-ID) y por IP (fallback anti-DDoS)
DEVICE_RATE_LIMIT = 60       # 60 req/min por dispositivo (Watch refresha cada ~10s = 6/min)
IP_RATE_LIMIT = 300          # 300 req/min por IP (generoso: muchos usuarios pueden compartir IP)
MAX_DEVICE_ID_LEN = 64       # Longitud máxima de X-Device-ID (UUID = 36 chars)
MAX_BUCKETS = 50_000         # Límite de buckets para prevenir DoS por memoria


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting por dispositivo (X-Device-ID) con fallback a IP.

    - Si el cliente envía X-Device-ID: limita a 60/min por dispositivo
    - Si no: limita a 300/min por IP (protección anti-DDoS bruta)

    El Watch genera un UUID al instalarse y lo envía siempre como header.
    """

    def __init__(self, app, device_limit: int = DEVICE_RATE_LIMIT, ip_limit: int = IP_RATE_LIMIT, window: int = 60):
        super().__init__(app)
        self.device_limit = device_limit
        self.ip_limit = ip_limit
        self.window = window
        self.buckets: dict[str, list[float]] = defaultdict(list)
        self.last_cleanup = time.time()

    def _get_key_and_limit(self, request: Request) -> tuple[str, int]:
        """Determina la clave de rate limiting y su límite."""
        device_id = request.headers.get("X-Device-ID")
        if device_id and len(device_id) <= MAX_DEVICE_ID_LEN:
            return f"device:{device_id}", self.device_limit
        client_ip = request.client.host if request.client else "unknown"
        return f"ip:{client_ip}", self.ip_limit

    async def dispatch(self, request: Request, call_next):
        key, limit = self._get_key_and_limit(request)
        now = time.time()

        # Limpieza periódica (cada 5 min) o forzada si se excede el límite de buckets
        if now - self.last_cleanup > 300 or len(self.buckets) > MAX_BUCKETS:
            stale = [k for k, ts in self.buckets.items() if not ts or now - ts[-1] > self.window]
            for k in stale:
                del self.buckets[k]
            self.last_cleanup = now

        # Filtrar timestamps dentro de la ventana
        self.buckets[key] = [t for t in self.buckets[key] if now - t < self.window]

        if len(self.buckets[key]) >= limit:
            return JSONResponse(
                status_code=429,
                content={"detail": f"Demasiadas peticiones. Máximo {limit}/min."},
                headers={"Retry-After": "60"},
            )

        self.buckets[key].append(now)
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Gestiona el ciclo de vida de la aplicación.
    Se ejecuta al iniciar y al cerrar.
    """
    # Inicializar base de datos al iniciar
    await database.init_db()
    # Arrancar scheduler de sincronización semanal
    start_scheduler()
    yield
    # Parar scheduler y cerrar cliente HTTP al apagar
    stop_scheduler()
    await tussam_service.close()


app = FastAPI(
    title="TUSSAM API",
    description="API para obtener horarios y paradas de TUSSAM (Sevilla)",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Middlewares (orden inverso de ejecución: el último añadido se ejecuta primero)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)


@app.get("/")
async def root():
    """Página principal con información básica."""
    return {"message": "TUSSAM API", "version": "1.0.0", "docs": "/docs"}


@app.get("/health")
async def health():
    """Health check para Docker/load balancers."""
    try:
        paradas = await database.get_all_paradas_from_db()
        return {"status": "ok", "paradas_en_db": len(paradas)}
    except Exception as e:
        logger.error("Health check fallido: %s", e)
        raise HTTPException(status_code=503, detail="DB no disponible")


@app.get("/paradas")
async def get_all_paradas():
    """
    Obtiene todas las paradas de TUSSAM.

    Returns:
        Lista de todas las paradas con código, nombre y coordenadas.
    """
    return await tussam_service.get_all_paradas()


@app.get("/paradas/cercanas")
async def get_paradas_cercanas(
    lat: float = Query(..., description="Latitud de la ubicación"),
    lon: float = Query(..., description="Longitud de la ubicación"),
    radio: int = Query(500, ge=50, le=2000, description="Radio de búsqueda en metros (50-2000)"),
    bearing: float = Query(
        None, description="Orientación del usuario en grados (0-360°)"
    ),
    bearing_tolerance: float = Query(
        60, ge=0, le=180, description="Tolerancia de orientación en grados (0-180)"
    ),
):
    """
    Obtiene las paradas cercanas a una ubicación.

    A diferencia de /cercanas, este endpoint NO incluye los tiempos de llegada.
    Útil cuando solo necesitas las coordenadas para mostrar en un mapa.
    """
    if not (-90 <= lat <= 90):
        raise HTTPException(status_code=400, detail="Latitud inválida")
    if not (-180 <= lon <= 180):
        raise HTTPException(status_code=400, detail="Longitud inválida")
    if bearing is not None and not (0 <= bearing <= 360):
        raise HTTPException(status_code=400, detail="Bearing debe estar entre 0 y 360")

    return await tussam_service.get_paradas_cercanas(
        lat, lon, radio, bearing, bearing_tolerance
    )


@app.get("/cercanas")
async def get_paradas_cercanas_con_tiempos(
    lat: float = Query(..., description="Latitud de la ubicación"),
    lon: float = Query(..., description="Longitud de la ubicación"),
    radio: int = Query(300, ge=50, le=2000, description="Radio en metros (50-2000)"),
    max_paradas: int = Query(3, ge=1, le=10, description="Máximo de paradas (1-10)"),
    bearing: float = Query(
        None, description="Orientación del usuario (0-360°) para filtrar dirección"
    ),
    bearing_tolerance: float = Query(
        60, ge=0, le=180, description="Tolerancia de orientación en grados (0-180)"
    ),
    tiempo_max: int = Query(
        None, ge=0, description="Filtrar buses que lleguen en máximo X minutos"
    ),
    lineas: str = Query(None, description="Filtrar solo estas líneas (ej: '01,C4,21')"),
    sentido: int = Query(None, description="Filtrar por sentido (1 o 2)"),
    formato: str = Query("json", description="Formato de respuesta: json, geojson"),
    incluir_mapa: bool = Query(False, description="Incluir URL de OpenStreetMap"),
):
    """
    Endpoint optimizado para AppleWatch.

    Devuelve las paradas cercanas CON sus tiempos de llegada en UNA sola llamada.
    Este es el endpoint principal para apps móviles y AppleWatch.
    """
    # Validaciones
    if not (-90 <= lat <= 90):
        raise HTTPException(status_code=400, detail="Latitud inválida")
    if not (-180 <= lon <= 180):
        raise HTTPException(status_code=400, detail="Longitud inválida")
    if bearing is not None and not (0 <= bearing <= 360):
        raise HTTPException(status_code=400, detail="Bearing debe estar entre 0 y 360")
    if formato not in ["json", "geojson"]:
        raise HTTPException(status_code=400, detail="Formato no soportado")
    if sentido is not None and sentido not in [1, 2]:
        raise HTTPException(status_code=400, detail="Sentido debe ser 1 o 2")

    # Procesar filtro de líneas
    lineas_filtro = lineas.upper().split(",") if lineas else None

    # Obtener paradas cercanas
    paradas = await tussam_service.get_paradas_cercanas(
        lat, lon, radio, bearing, bearing_tolerance
    )

    # Filtrar por bearing si se especifica
    if bearing is not None:
        paradas = [
            p for p in paradas if p.get("bearing_diff", 999) <= bearing_tolerance
        ]

    # Limitar número de paradas
    paradas = paradas[:max_paradas]

    # Procesar cada parada: obtener tiempos
    resultado = []
    for p in paradas:
        # Obtener tiempos (con cache automático + error handling)
        try:
            tiempos = await tussam_service.get_tiempos_parada(p["codigo"])
        except (httpx.HTTPError, httpx.TimeoutException):
            logger.warning("TUSSAM API no disponible para parada %s", p["codigo"])
            tiempos = {"tiempos": []}
        except Exception:
            logger.exception("Error inesperado obteniendo tiempos para parada %s", p["codigo"])
            tiempos = {"tiempos": []}

        tiempos_filtrados = tiempos.get("tiempos", [])

        # Aplicar filtros
        if tiempo_max is not None:
            tiempos_filtrados = [
                t for t in tiempos_filtrados if 0 <= t["tiempo_minutos"] <= tiempo_max
            ]
        if lineas_filtro:
            tiempos_filtrados = [
                t for t in tiempos_filtrados if t["linea"] in lineas_filtro
            ]
        if sentido is not None:
            tiempos_filtrados = [
                t for t in tiempos_filtrados if t.get("sentido") == sentido
            ]

        # Construir datos de la parada (solo datos de DB, sin geocoding en caliente)
        parada_data = {
            "codigo": p["codigo"],
            "nombre": p["nombre"],
            "latitud": p["latitud"],
            "longitud": p["longitud"],
            "distancia": p["distancia"],
            "bearing": p.get("bearing"),
            "bearing_diff": p.get("bearing_diff"),
            "calle": p.get("calle", ""),
            "direccion_completa": p.get("direccion_completa", ""),
            "tiempos": tiempos_filtrados[:5],
        }

        if incluir_mapa:
            parada_data["mapa_url"] = (
                f"https://www.openstreetmap.org/"
                f"?mlat={p['latitud']}&mlon={p['longitud']}"
                f"#map=18/{p['latitud']}/{p['longitud']}"
            )

        resultado.append(parada_data)

    response_data = {
        "ubicacion": {"lat": lat, "lon": lon, "bearing": bearing},
        "paradas": resultado,
    }

    if formato == "geojson":
        response_data = _convert_to_geojson(response_data)

    return response_data


@app.get("/paradas/{codigo}")
async def get_parada(codigo: str):
    """
    Obtiene una parada específica por su código.

    Args:
        codigo: Código de la parada (ej: "43", "183")

    Returns:
        Datos de la parada

    Raises:
        HTTPException 404: Si la parada no existe
    """
    parada = await tussam_service.get_parada_by_codigo(codigo)
    if not parada:
        raise HTTPException(status_code=404, detail="Parada no encontrada")
    return parada


@app.get("/paradas/{codigo}/tiempos")
async def get_tiempos(codigo: str):
    """Obtiene los tiempos de llegada de autobuses a una parada."""
    try:
        return await tussam_service.get_tiempos_parada(codigo)
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("TUSSAM API error para parada %s: %s", codigo, e)
        raise HTTPException(
            status_code=503,
            detail="TUSSAM API no disponible. Inténtalo en unos segundos.",
        )
    except Exception:
        logger.exception("Error inesperado obteniendo tiempos para parada %s", codigo)
        raise HTTPException(status_code=500, detail="Error interno")


@app.get("/lineas")
async def get_lineas():
    """
    Obtiene todas las líneas de TUSSAM.

    Returns:
        Lista de líneas con número, nombre y color
    """
    return await tussam_service.get_lineas()


@app.get("/lineas/{linea_numero}/paradas")
async def get_paradas_de_linea(linea_numero: str):
    """
    Obtiene las paradas de una línea específica, ordenadas por sentido y recorrido.

    Args:
        linea_numero: Número de la línea (ej: "01", "C4", "21")
    """
    return await tussam_service.get_paradas_de_linea(linea_numero)


@app.get("/paradas/{codigo}/lineas")
async def get_lineas_de_parada(codigo: str):
    """
    Obtiene las líneas que pasan por una parada específica.

    Args:
        codigo: Código de la parada (ej: "252")
    """
    return await tussam_service.get_lineas_de_parada(codigo)


# ============================================
# Endpoints de Sincronización
# ============================================


@app.post("/sync/paradas", dependencies=[Depends(verify_sync_key)])
async def sync_paradas():
    """Sincroniza las paradas desde la API de TUSSAM. Requiere X-API-Key."""
    count = await tussam_service.sync_paradas_from_api()
    return {"message": f"Se sincronizaron {count} paradas"}


@app.post("/sync/lineas", dependencies=[Depends(verify_sync_key)])
async def sync_lineas():
    """Sincroniza las líneas desde la API de TUSSAM. Requiere X-API-Key."""
    count = await tussam_service.sync_lineas_from_api()
    return {"message": f"Se sincronizaron {count} líneas"}


@app.post("/sync/all", dependencies=[Depends(verify_sync_key)])
async def sync_all():
    """Sincroniza todo (paradas + líneas + relaciones). Requiere X-API-Key."""
    count_paradas = await tussam_service.sync_paradas_from_api()
    count_lineas = await tussam_service.sync_lineas_from_api()
    count_relaciones = await tussam_service.sync_paradas_lineas_from_api()
    return {
        "message": "Sincronización completa",
        "paradas": count_paradas,
        "lineas": count_lineas,
        "paradas_lineas": count_relaciones,
    }


@app.post("/sync/paradas-lineas", dependencies=[Depends(verify_sync_key)])
async def sync_paradas_lineas():
    """Sincroniza la relación paradas-líneas. Requiere X-API-Key."""
    count = await tussam_service.sync_paradas_lineas_from_api()
    return {"message": f"Se sincronizaron {count} relaciones parada-línea"}


@app.post("/sync/direcciones", dependencies=[Depends(verify_sync_key)])
async def sync_direcciones():
    """Geocodifica paradas sin dirección (~4 min). Requiere X-API-Key."""
    result = await tussam_service.sync_direcciones_all()
    return {
        "message": "Geocodificación completada",
        "total": result["total"],
        "ok": result["ok"],
        "errors": result["errors"],
    }


# ============================================
# Funciones Auxiliares
# ============================================


def _convert_to_geojson(data: dict) -> dict:
    """
    Convierte la respuesta JSON a formato GeoJSON.

    Útil para integrar con libraries de mapas.

    Args:
        data: Respuesta JSON de /cercanas

    Returns:
        FeatureCollection en formato GeoJSON
    """
    features = []
    for parada in data.get("paradas", []):
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [parada["longitud"], parada["latitud"]],
            },
            "properties": {
                "codigo": parada["codigo"],
                "nombre": parada["nombre"],
                "distancia": parada["distancia"],
                "tiempos": parada.get("tiempos", []),
            },
        }
        features.append(feature)

    return {"type": "FeatureCollection", "features": features}
