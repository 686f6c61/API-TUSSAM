"""
TUSSAM API - Aplicación Principal
================================

Puntos de entrada (endpoints) de la API.
Expone los servicios de TUSSAM a través de HTTP.

Autor: 686f6c61 (https://github.com/686f6c61)
Versión: 2.0.0
Licencia: PolyForm Noncommercial 1.0.0 (uso no comercial)
"""

import os
import hmac
import time
import asyncio
import logging
import httpx
import re
from collections import defaultdict

from fastapi import FastAPI, Query, HTTPException, Request, Depends, Security, Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from contextlib import asynccontextmanager
from app.services.tussam import tussam_service
from app import database
from app.env import env_bool, env_csv
from app.scheduler import start_scheduler, stop_scheduler

logger = logging.getLogger("tussam.api")

APP_VERSION = "2.0.0"
DEFAULT_SYNC_API_KEY = "cambia-esta-clave"


APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV in {"prod", "production"}

# Patrones de validación de parámetros de ruta. El código de parada de TUSSAM es
# numérico; el número de línea es alfanumérico (p. ej. "01", "C4", "A2"). Validar
# el formato en el borde evita que caracteres de control (CRLF -> inyección en
# logs) o metacaracteres de URL lleguen a interpolarse en la petición saliente
# hacia el origen, y acota el universo de claves de cache y de locks en memoria.
CODIGO_PARADA_PATTERN = r"^\d{1,7}$"
LINEA_NUMERO_PATTERN = r"^[A-Za-z0-9]{1,8}$"

# Rutas operativas exentas de rate limiting: los sondeos de salud del
# balanceador no deben consumir la cuota de nadie.
RATE_LIMIT_EXEMPT_PATHS = frozenset({"/health"})


class ParadaOut(BaseModel):
    codigo: str
    nombre: str
    latitud: float
    longitud: float
    calle: str | None = None
    numero: str | None = None
    codigo_postal: str | None = None
    municipio: str | None = None
    provincia: str | None = None
    comunidad_autonoma: str | None = None
    direccion_completa: str | None = None
    updated_at: str | None = None


class ParadaCercanaOut(ParadaOut):
    distancia: int
    bearing: int | None = None
    bearing_diff: int | None = None


class ParadaLineaOut(ParadaOut):
    sentido: int
    orden: int


class LineaOut(BaseModel):
    numero: str
    nombre: str
    color: str
    sublinea: int | None = None
    hora_inicio_ida: str | None = None
    hora_fin_ida: str | None = None
    hora_inicio_vuelta: str | None = None
    hora_fin_vuelta: str | None = None
    updated_at: str | None = None


class TiempoOut(BaseModel):
    linea: str
    color: str
    tiempo_minutos: int
    destino: str
    distancia_metros: int | None = None
    vehiculo: str | int | None = None
    atributos: list = Field(default_factory=list)
    sentido: int | None = None


class TiemposParadaOut(BaseModel):
    parada: str
    nombre: str
    latitud: float | None = None
    longitud: float | None = None
    tiempos: list[TiempoOut]
    stale: bool | None = None
    cached_at: str | None = None
    upstream_status: str | None = None
    upstream_detail: str | None = None

# --- Autenticación para endpoints de sync ---
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_sync_key(api_key: str = Security(api_key_header)):
    """
    Verifica la API key para endpoints de sincronización.

    Usa hmac.compare_digest en lugar de == para prevenir timing attacks:
    aunque un atacante mida el tiempo de respuesta, no puede deducir la clave.
    """
    expected = os.getenv("SYNC_API_KEY", "")
    if not expected:
        if env_bool("ALLOW_UNAUTHENTICATED_SYNC", False):
            # Este flag abre la escritura a cualquiera; jamás debe usarse en
            # producción. Si se detecta activo con APP_ENV productivo, se rechaza.
            if IS_PRODUCTION:
                logger.error(
                    "ALLOW_UNAUTHENTICATED_SYNC activo en producción: rechazado"
                )
                raise HTTPException(
                    status_code=503,
                    detail="Configuración insegura: sync sin autenticar no permitido en producción",
                )
            logger.warning(
                "ALLOW_UNAUTHENTICATED_SYNC activo: endpoints de sync sin API key"
            )
            return
        logger.error("SYNC_API_KEY no configurada: rechazando endpoint de sync")
        raise HTTPException(
            status_code=503,
            detail="SYNC_API_KEY no configurada",
        )
    if expected == DEFAULT_SYNC_API_KEY:
        logger.error("SYNC_API_KEY usa el valor de ejemplo: rechazando endpoint de sync")
        raise HTTPException(
            status_code=503,
            detail="SYNC_API_KEY insegura",
        )
    if not api_key or not hmac.compare_digest(api_key, expected):
        raise HTTPException(status_code=403, detail="API key inválida o ausente")


# --- Rate limiting ---
# Dos cubos que se aplican SIEMPRE de forma conjunta: uno por IP (techo
# anti-DDoS) y otro, más estricto, por dispositivo (X-Device-ID). El
# X-Device-ID lo elige el cliente, así que NO puede sustituir al límite por IP:
# de lo contrario, rotar el identificador en cada petición saltaría el control.
# Aquí el dispositivo solo puede ser MÁS restrictivo, nunca una vía de escape.
DEVICE_RATE_LIMIT = 60       # 60 req/min por dispositivo (clientes frecuentes ~6/min)
IP_RATE_LIMIT = 300          # 300 req/min por IP (generoso: muchos usuarios pueden compartir IP)
MAX_DEVICE_ID_LEN = 64       # Longitud máxima de X-Device-ID (UUID = 36 chars)
MAX_BUCKETS = 50_000         # Límite de buckets para prevenir DoS por memoria
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")

# IPs de proxys de confianza de los que aceptamos X-Forwarded-For. Vacío por
# defecto: sin configurar, se usa la IP de conexión directa (comportamiento
# seguro). Detrás de un proxy hay que fijar TRUSTED_PROXY_IPS con su IP para que
# el rate limiting distinga clientes reales en vez de limitar por la IP del proxy.
TRUSTED_PROXY_IPS = frozenset(env_csv("TRUSTED_PROXY_IPS"))


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting combinado por IP y por dispositivo.

    Cada petición se contabiliza en el cubo de su IP (límite 300/min) y, si trae
    un X-Device-ID válido, también en el cubo de ese dispositivo (límite 60/min).
    Se rechaza (429) si CUALQUIERA de los dos cubos supera su límite. Así el
    identificador de dispositivo nunca permite exceder el techo por IP.

    Advertencia de escalado: los cubos viven en memoria del proceso. Con varios
    workers, cada uno mantiene los suyos y el límite efectivo se multiplica por
    el número de workers. Para un límite global real hay que externalizar el
    estado (p. ej. Redis) o desplegar con un solo worker y escalar por réplicas.
    """

    def __init__(self, app, device_limit: int = DEVICE_RATE_LIMIT, ip_limit: int = IP_RATE_LIMIT, window: int = 60):
        super().__init__(app)
        self.device_limit = device_limit
        self.ip_limit = ip_limit
        self.window = window
        self.buckets: dict[str, list[float]] = defaultdict(list)
        self.last_cleanup = time.time()

    def _client_ip(self, request: Request) -> str:
        """Resuelve la IP del cliente respetando proxys de confianza.

        Solo se hace caso a X-Forwarded-For si la conexión entrante procede de
        una IP declarada en TRUSTED_PROXY_IPS; de lo contrario, el header es
        falsificable y se ignora para no permitir evadir el límite spoofeando IPs.
        """
        peer = request.client.host if request.client else "unknown"
        if peer in TRUSTED_PROXY_IPS:
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                # El primer valor es el cliente original más cercano al proxy.
                return forwarded.split(",")[0].strip() or peer
        return peer

    def _valid_device_id(self, request: Request) -> str | None:
        """Devuelve el X-Device-ID si es válido, o None."""
        device_id = request.headers.get("X-Device-ID")
        if (
            device_id
            and len(device_id) <= MAX_DEVICE_ID_LEN
            and DEVICE_ID_RE.fullmatch(device_id)
        ):
            return device_id
        return None

    def _headers(self, limit: int, remaining: int, reset_seconds: int) -> dict[str, str]:
        """Cabeceras estándar para que los clientes ajusten su frecuencia."""
        return {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(max(0, remaining)),
            "X-RateLimit-Reset": str(max(0, reset_seconds)),
        }

    def _prune(self, key: str, now: float) -> None:
        """Descarta del cubo los timestamps fuera de la ventana."""
        self.buckets[key] = [t for t in self.buckets[key] if now - t < self.window]

    def _reset_seconds(self, key: str, now: float) -> int:
        """Segundos hasta que el cubo vuelva a tener hueco."""
        return (
            int(self.window - (now - self.buckets[key][0]))
            if self.buckets[key]
            else self.window
        )

    async def dispatch(self, request: Request, call_next):
        """
        Aplica el rate limiting combinado (IP + dispositivo) a cada petición.

        Las rutas exentas (health checks) se dejan pasar sin contabilizar.
        """
        if request.url.path in RATE_LIMIT_EXEMPT_PATHS:
            return await call_next(request)

        now = time.time()

        # Limpieza periódica: eliminar cubos inactivos para no acumular memoria.
        # Se ejecuta cada 5 minutos o si hay más de MAX_BUCKETS cubos (ataque DoS).
        if now - self.last_cleanup > 300 or len(self.buckets) > MAX_BUCKETS:
            stale = [k for k, ts in self.buckets.items() if not ts or now - ts[-1] > self.window]
            for k in stale:
                del self.buckets[k]
            self.last_cleanup = now

        # Construir la lista de cubos aplicables: siempre la IP; el dispositivo
        # solo se añade como límite adicional más estricto.
        ip = self._client_ip(request)
        device_id = self._valid_device_id(request)
        checks = [(f"ip:{ip}", self.ip_limit)]
        if device_id:
            checks.append((f"device:{device_id}", self.device_limit))

        # Si CUALQUIER cubo ya está lleno, se rechaza sin registrar la petición.
        # No contabilizar la petición rechazada es deliberado: evita que un
        # dispositivo abusivo (ya bloqueado por su propio cubo de 60/min) siga
        # consumiendo la cuota compartida de su IP y provoque bloqueos colaterales
        # a otros dispositivos legítimos tras el mismo NAT. Para exceder el techo
        # de IP haría falta rotar identificadores, y entonces sí se contabiliza.
        for key, limit in checks:
            self._prune(key, now)
            if len(self.buckets[key]) >= limit:
                reset_seconds = self._reset_seconds(key, now)
                headers = self._headers(limit, 0, reset_seconds)
                headers["Retry-After"] = str(max(1, reset_seconds))
                return JSONResponse(
                    status_code=429,
                    content={"detail": f"Demasiadas peticiones. Máximo {limit}/min."},
                    headers=headers,
                )

        # Registrar la petición en todos los cubos aplicables.
        for key, _ in checks:
            self.buckets[key].append(now)

        response = await call_next(request)

        # Reportar la cuota del cubo más estricto (el de menor holgura relativa),
        # que es el que primero limitará al cliente. Con dispositivo válido suele
        # ser el de dispositivo (60); sin él, el de IP (300).
        report_key, report_limit = min(
            checks, key=lambda c: c[1] - len(self.buckets[c[0]])
        )
        response.headers.update(
            self._headers(
                report_limit,
                report_limit - len(self.buckets[report_key]),
                self._reset_seconds(report_key, now),
            )
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Añade cabeceras de seguridad HTTP a todas las respuestas.

    Aunque la API sirve JSON, la documentación interactiva (Swagger/ReDoc) sí
    devuelve HTML; estas cabeceras endurecen el navegador frente a sniffing de
    tipo MIME, clickjacking y fuga de referrers, y activan HSTS cuando procede.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if IS_PRODUCTION:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Gestiona el ciclo de vida de la aplicación.
    Se ejecuta al iniciar y al cerrar.
    """
    await database.init_db()
    start_scheduler()
    yield
    stop_scheduler()
    await tussam_service.close()
    await database.close_db()


docs_enabled = env_bool("ENABLE_DOCS", default=not IS_PRODUCTION)

app = FastAPI(
    title="TUSSAM API",
    description="API para obtener horarios y paradas de TUSSAM (Sevilla)",
    version=APP_VERSION,
    docs_url="/docs" if docs_enabled else None,
    redoc_url="/redoc" if docs_enabled else None,
    openapi_url="/openapi.json" if docs_enabled else None,
    lifespan=lifespan,
)

# Middlewares (orden inverso de ejecución: el último añadido se ejecuta primero)
allowed_hosts = env_csv("ALLOWED_HOSTS")
if allowed_hosts:
    # localhost y 127.0.0.1 se añaden siempre: el health check del contenedor y
    # de los balanceadores golpea http://localhost/health, y sin estos hosts en
    # la lista TrustedHostMiddleware lo rechazaría con 400, marcando la instancia
    # como no saludable en un bucle de reinicios.
    for local_host in ("localhost", "127.0.0.1"):
        if local_host not in allowed_hosts:
            allowed_hosts.append(local_host)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

app.add_middleware(
    CORSMiddleware,
    allow_origins=env_csv("CORS_ORIGINS", default="" if IS_PRODUCTION else "*"),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)


@app.get("/")
async def root():
    """Página principal con información básica."""
    return {"message": "TUSSAM API", "version": APP_VERSION, "docs": app.docs_url}


@app.get("/health")
async def health():
    """Health check para Docker y balanceadores. Verifica DB y estado general.

    Devuelve 503 si la base de datos no responde. Si responde pero está vacía
    (despliegue nuevo sin sync inicial), el estado se degrada a ``no_data`` para
    que la instancia no reciba tráfico como si estuviera plenamente operativa.
    """
    db_ok = await database.db_health()
    if not db_ok:
        raise HTTPException(status_code=503, detail="DB no disponible")
    try:
        paradas_count = await database.count_paradas()
    except Exception:
        logger.exception("Health check: fallo al contar paradas")
        raise HTTPException(status_code=503, detail="DB no disponible")

    status = "ok" if paradas_count > 0 else "no_data"
    return {
        "status": status,
        "db": "connected",
        "paradas_en_db": paradas_count,
        "version": APP_VERSION,
    }


@app.get("/paradas", response_model=list[ParadaOut])
async def get_all_paradas():
    """
    Obtiene todas las paradas de TUSSAM.

    Returns:
        Lista de todas las paradas con código, nombre y coordenadas.
    """
    return await tussam_service.get_all_paradas()


@app.get("/paradas/cercanas", response_model=list[ParadaCercanaOut])
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
    Endpoint agregado para clientes que necesitan minimizar llamadas HTTP.

    Devuelve las paradas cercanas CON sus tiempos de llegada en UNA sola llamada.
    Este es el endpoint principal para apps, webs e integraciones.
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

    # Procesar filtro de líneas (normalizando espacios: "01, C4" -> {"01","C4"})
    lineas_filtro = (
        {x.strip() for x in lineas.upper().split(",") if x.strip()} if lineas else None
    )

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

    # Obtener los tiempos de todas las paradas en paralelo. El single-flight por
    # parada y el semáforo del cliente HTTP siguen protegiendo al origen; aquí
    # solo evitamos pagar N latencias en serie.
    tiempos_por_parada = await asyncio.gather(
        *(_get_tiempos_seguro(p["codigo"]) for p in paradas)
    )

    resultado = []
    for p, tiempos in zip(paradas, tiempos_por_parada):
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

        # Construir datos de la parada (lee directamente de la tabla paradas)
        calle = p.get("calle") or ""
        numero = p.get("numero") or ""
        direccion_completa = f"{calle} {numero}".strip() if calle else ""

        parada_data = {
            "codigo": p["codigo"],
            "nombre": p["nombre"],
            "latitud": p["latitud"],
            "longitud": p["longitud"],
            "distancia": p["distancia"],
            "bearing": p.get("bearing"),
            "bearing_diff": p.get("bearing_diff"),
            "calle": calle,
            "numero": numero,
            "codigo_postal": p.get("codigo_postal"),
            "municipio": p.get("municipio"),
            "direccion_completa": direccion_completa,
            # Estado del origen para ESTA parada: el cliente debe poder distinguir
            # "no vienen buses" (ok con lista vacía) de "no pudimos consultar"
            # (unavailable/error), y saber si los datos son antiguos (stale).
            "tiempos_status": tiempos.get("tiempos_status", "ok"),
            "tiempos": tiempos_filtrados[:5],
        }
        if tiempos.get("stale"):
            parada_data["stale"] = True
            parada_data["cached_at"] = tiempos.get("cached_at")

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


@app.get("/paradas/{codigo}", response_model=ParadaOut)
async def get_parada(
    codigo: str = Path(..., pattern=CODIGO_PARADA_PATTERN, description="Código numérico de la parada"),
):
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


@app.get(
    "/paradas/{codigo}/tiempos",
    response_model=TiemposParadaOut,
    response_model_exclude_none=True,
)
async def get_tiempos(
    codigo: str = Path(..., pattern=CODIGO_PARADA_PATTERN, description="Código numérico de la parada"),
):
    """Obtiene los tiempos de llegada de autobuses a una parada."""
    try:
        return await tussam_service.get_tiempos_parada(codigo)
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("TUSSAM API error para parada %s: %s", codigo, e)
        parada = await tussam_service.get_parada_by_codigo(codigo)
        if not parada:
            raise HTTPException(status_code=404, detail="Parada no encontrada")
        return {
            "parada": codigo,
            "nombre": parada["nombre"],
            "latitud": parada["latitud"],
            "longitud": parada["longitud"],
            "tiempos": [],
            "upstream_status": "unavailable",
            "upstream_detail": "TUSSAM API no disponible. Inténtalo en unos segundos.",
        }
    except Exception:
        logger.exception("Error inesperado obteniendo tiempos para parada %s", codigo)
        raise HTTPException(status_code=500, detail="Error interno")


@app.get("/lineas", response_model=list[LineaOut])
async def get_lineas():
    """
    Obtiene todas las líneas de TUSSAM.

    Returns:
        Lista de líneas con número, nombre y color
    """
    return await tussam_service.get_lineas()


@app.get("/lineas/{linea_numero}/paradas", response_model=list[ParadaLineaOut])
async def get_paradas_de_linea(
    linea_numero: str = Path(..., pattern=LINEA_NUMERO_PATTERN, description="Número de línea (ej: 01, C4)"),
):
    """
    Obtiene las paradas de una línea específica, ordenadas por sentido y recorrido.

    Args:
        linea_numero: Número de la línea (ej: "01", "C4", "21")
    """
    return await tussam_service.get_paradas_de_linea(linea_numero)


@app.get("/paradas/{codigo}/lineas", response_model=list[str])
async def get_lineas_de_parada(
    codigo: str = Path(..., pattern=CODIGO_PARADA_PATTERN, description="Código numérico de la parada"),
):
    """
    Obtiene las líneas que pasan por una parada específica.

    Args:
        codigo: Código de la parada (ej: "252")
    """
    return await tussam_service.get_lineas_de_parada(codigo)


# ============================================
# Endpoints de Sincronización
# ============================================

# La sincronización se serializa con un lock compartido que vive en el servicio
# (tussam_service.get_sync_lock), de modo que tanto estos endpoints como el job
# del scheduler compiten por el mismo cerrojo y nunca se solapan sobre el
# catálogo. Si ya hay una en curso, el endpoint responde 409.


@asynccontextmanager
async def _sync_guard():
    """Serializa las sincronizaciones: 409 si ya hay una en curso.

    Traduce además los ``RuntimeError`` que emiten los syncs cuando el origen no
    devuelve datos (Cloudflare, contrato cambiado) a un ``502 Bad Gateway`` con
    el motivo, en lugar de dejar que se conviertan en un ``500`` genérico y opaco.
    """
    lock = tussam_service.get_sync_lock()
    if lock.locked():
        raise HTTPException(
            status_code=409,
            detail="Ya hay una sincronización en curso. Inténtalo más tarde.",
        )
    async with lock:
        try:
            yield
        except RuntimeError as e:
            logger.warning("Sync abortado: %s", e)
            raise HTTPException(status_code=502, detail=str(e))


@app.post("/sync/paradas", dependencies=[Depends(verify_sync_key)])
async def sync_paradas():
    """Sincroniza las paradas desde la API de TUSSAM. Requiere X-API-Key."""
    async with _sync_guard():
        count = await tussam_service.sync_paradas_from_api()
    return {"message": f"Se sincronizaron {count} paradas"}


@app.post("/sync/lineas", dependencies=[Depends(verify_sync_key)])
async def sync_lineas():
    """Sincroniza las líneas desde la API de TUSSAM. Requiere X-API-Key."""
    async with _sync_guard():
        count = await tussam_service.sync_lineas_from_api()
    return {"message": f"Se sincronizaron {count} líneas"}


@app.post("/sync/all", dependencies=[Depends(verify_sync_key)])
async def sync_all():
    """Sincroniza todo (paradas + líneas + relaciones). Requiere X-API-Key.

    Reporta el resultado por fase: si una fase falla, las anteriores que ya se
    completaron con éxito conservan su recuento y la fallida se marca con su
    motivo, en lugar de perder todo el trabajo en un único error.
    """
    resultado: dict = {"message": "Sincronización completa"}
    async with _sync_guard():
        for clave, accion in (
            ("paradas", tussam_service.sync_paradas_from_api),
            ("lineas", tussam_service.sync_lineas_from_api),
            ("paradas_lineas", tussam_service.sync_paradas_lineas_from_api),
        ):
            try:
                resultado[clave] = await accion()
            except RuntimeError as e:
                logger.warning("Sync '%s' abortado: %s", clave, e)
                resultado[clave] = f"error: {e}"
                resultado["message"] = "Sincronización parcial"
    return resultado


@app.post("/sync/paradas-lineas", dependencies=[Depends(verify_sync_key)])
async def sync_paradas_lineas():
    """Sincroniza la relación paradas-líneas. Requiere X-API-Key."""
    async with _sync_guard():
        count = await tussam_service.sync_paradas_lineas_from_api()
    return {"message": f"Se sincronizaron {count} relaciones parada-línea"}


@app.post("/sync/direcciones", dependencies=[Depends(verify_sync_key)])
async def sync_direcciones():
    """Geocodifica paradas sin dirección (~4 min). Requiere X-API-Key."""
    async with _sync_guard():
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


async def _get_tiempos_seguro(codigo: str) -> dict:
    """Obtiene los tiempos de una parada señalizando el estado del origen.

    A diferencia de propagar la excepción, devuelve siempre un dict con la clave
    ``tiempos_status`` para que ``/cercanas`` distinga con claridad tres casos:

    - ``ok``: respuesta normal (la lista puede estar vacía si no vienen buses).
    - ``unavailable``: el origen no respondió y no había cache que servir.
    - ``error``: fallo inesperado al procesar la parada.

    Los metadatos de cache antigua (``stale``, ``cached_at``) se conservan tal
    cual los devuelve el servicio.
    """
    try:
        tiempos = await tussam_service.get_tiempos_parada(codigo)
        tiempos.setdefault("tiempos_status", "ok")
        return tiempos
    except (httpx.HTTPError, httpx.TimeoutException):
        logger.warning("TUSSAM API no disponible para parada %s", codigo)
        return {"tiempos": [], "tiempos_status": "unavailable"}
    except Exception:
        logger.exception("Error inesperado obteniendo tiempos para parada %s", codigo)
        return {"tiempos": [], "tiempos_status": "error"}


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
