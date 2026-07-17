"""
TUSSAM API - Servicio de TUSSAM
==============================

Cliente para la API de TUSSAM y Nominatim (OpenStreetMap).
Gestiona:
- Obtención de paradas desde la API de TUSSAM
- Obtención de tiempos de llegada
- Geocodificación de coordenadas a direcciones
- Sincronización de datos

Autor: 686f6c61 (https://github.com/686f6c61)
Versión: 2.0.0
Licencia: PolyForm Noncommercial 1.0.0 (uso no comercial)
"""

import httpx
import json
from datetime import datetime
from typing import List, Optional
import app.database as db
import logging
import asyncio
import math

from app.env import env_int, env_float

# La configuración del logging es responsabilidad del arranque de la aplicación
# (o del servidor: uvicorn/gunicorn). Llamar a logging.basicConfig al importar
# un módulo sobreescribiría esa configuración global de forma inesperada.
logger = logging.getLogger(__name__)

# URLs de las APIs externas
BASE_URL = "https://reddelineas.tussam.es"
NOMINATIM_API_URL = "https://nominatim.openstreetmap.org/reverse"


class TussamService:
    """
    Servicio principal para interactuar con la API de TUSSAM.

    Maneja:
    - Peticiones a la API de TUSSAM (tiempos, paradas, líneas)
    - Geocodificación con Nominatim (OpenStreetMap)
    - Cacheo de resultados
    """

    def __init__(self):
        self.max_concurrent_tussam_requests = env_int(
            "TUSSAM_MAX_CONCURRENT_REQUESTS", 4
        )
        self.sync_request_delay_seconds = env_float(
            "TUSSAM_SYNC_REQUEST_DELAY_SECONDS", 0.2
        )
        # Guardián de completitud del sync. La API de TUSSAM solo lista las líneas
        # ACTIVAS en el instante de la consulta, así que un sync en una franja de
        # baja actividad (madrugada) devuelve una fracción de la red. Si los datos
        # recibidos son menores que esta proporción del catálogo ya almacenado, el
        # sync se aborta sin tocar nada, para no degradar la red guardada. Con 0.8
        # se exige recuperar al menos el 80 % de lo que ya se conoce.
        self.sync_min_completeness_ratio = env_float(
            "SYNC_MIN_COMPLETENESS_RATIO", 0.8, minimum=0.0
        )
        self._tussam_semaphore = asyncio.Semaphore(
            self.max_concurrent_tussam_requests
        )
        self._tiempos_locks: dict[str, asyncio.Lock] = {}
        self._tiempos_locks_guard = asyncio.Lock()
        # Lock de sincronización compartido entre los endpoints /sync/* y el job
        # del scheduler, para que un sync manual y el semanal no se solapen sobre
        # el mismo catálogo. Perezoso para ligarlo al event loop correcto.
        self._sync_lock: Optional[asyncio.Lock] = None
        # follow_redirects=False: la ruta hacia el upstream incorpora el código
        # de parada. Seguir redirecciones a ciegas podría llevar la petición a un
        # host no previsto (SSRF) si el origen o un intermediario devolviera un
        # 3xx. La API de TUSSAM responde directamente sin redirigir.
        self.client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=False,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
                "Accept": "application/json",
                "Referer": "https://reddelineas.tussam.es/",
            },
        )
        self.base_url = BASE_URL

    def _guard_completeness(self, entidad: str, recibidos: int, actuales: int) -> None:
        """Aborta el sync si los datos recibidos son sospechosamente incompletos.

        Compara la cantidad recuperada del origen con la ya almacenada. Si hay
        catálogo previo (``actuales > 0``) y lo recibido no alcanza la proporción
        mínima configurada, lanza ``RuntimeError`` para preservar el último estado
        bueno en lugar de sustituirlo por una foto parcial (típico de sincronizar
        en una franja horaria con pocas líneas en servicio).

        Args:
            entidad: Nombre legible de lo que se sincroniza (para el mensaje).
            recibidos: Elementos obtenidos del origen en este sync.
            actuales: Elementos ya presentes en la base de datos.
        """
        if actuales <= 0:
            return  # Primera carga: no hay nada que proteger.
        minimo = actuales * self.sync_min_completeness_ratio
        if recibidos < minimo:
            raise RuntimeError(
                f"Sync de {entidad} incompleto: {recibidos} recibidos frente a "
                f"{actuales} almacenados (mínimo exigido {minimo:.0f}). "
                f"Probable sincronización en franja de baja actividad; "
                f"no se reemplaza el catálogo."
            )

    def get_sync_lock(self) -> asyncio.Lock:
        """Devuelve el lock de sincronización, creándolo de forma perezosa.

        Compartido por los endpoints /sync/* y el scheduler para garantizar que
        solo se ejecuta una sincronización a la vez en todo el proceso.
        """
        if self._sync_lock is None:
            self._sync_lock = asyncio.Lock()
        return self._sync_lock

    async def close(self):
        """Cierra el cliente HTTP al apagar la aplicación."""
        await self.client.aclose()

    def _format_datetime(self, dt: Optional[datetime] = None) -> str:
        """
        Formatea la fecha para la API de TUSSAM.

        Formato: DD-MM-AAAATHH:MM:SS (con : encodeado como %3A)
        """
        if dt is None:
            dt = datetime.now()
        return dt.strftime("%d-%m-%YT%H:%M:%S").replace(":", "%3A")

    def _coords_to_tussam(self, lat: float, lon: float) -> tuple:
        """
        Convierte coordenadas a formato de TUSSAM (multiplicado por 10^6).
        """
        lat_int = int(lat * 1000000)
        lon_int = int(lon * 1000000)
        return lat_int, lon_int

    async def sync_paradas_from_api(self) -> int:
        """
        Sync de paradas: itera por todas las líneas y sus nodos para obtener
        todas las paradas. La API de TUSSAM no expone un endpoint /paradas
        directo, así que hay que reconstruir el catálogo desde las líneas.

        Estrategia:
        1. Obtener lista de líneas (1 petición)
        2. Por cada línea, obtener nodos en ambos sentidos (2 peticiones × N líneas)
        3. Deducir paradas únicas por código (una parada aparece en varias líneas)

        Guarda los resultados en la base de datos SQLite.

        Returns:
            Número de paradas sincronizadas
        """
        logger.info("Fetching lineas from TUSSAM API")
        fh = self._format_datetime()

        # Paso 1: obtener la lista de líneas activas
        url_lineas = f"{self.base_url}/API/infotus-ui/lineas/{fh}"
        response = await self._get_with_retry(url_lineas)

        data_lineas = response.json()
        lineas_data = data_lineas.get("result", {})
        lineas = lineas_data.get("lineasDisponibles", [])
        logger.info(f"Found {len(lineas)} lineas")

        # Una lista de líneas vacía significa que el origen no devolvió datos
        # útiles (Cloudflare, cambio de contrato, 200 con HTML). Continuar
        # guardaría 0 paradas y reportaría un falso éxito, así que abortamos.
        if not lineas:
            raise RuntimeError(
                "TUSSAM no devolvió líneas disponibles; se aborta el sync de paradas"
            )

        todas_paradas: dict = {}
        fallos = 0

        # Paso 2: por cada línea, obtener los nodos (paradas) en ambos sentidos
        # Sentido 1 = ida, Sentido 2 = vuelta
        for linea in lineas:
            linea_num = linea.get("linea", 0)
            if not linea_num:
                continue

            for sentido in [1, 2]:
                try:
                    url_nodos = f"{self.base_url}/API/infotus-ui/nodosLinea/{linea_num}/{sentido}/{fh}"
                    resp = await self._get_with_retry(url_nodos)
                    data_nodos = resp.json()
                    nodos = data_nodos.get("result", [])

                    for nodo in nodos:
                        codigo = str(nodo.get("codigo", ""))
                        if not codigo or codigo in todas_paradas:
                            continue

                        posicion = nodo.get("posicion", {})
                        lat = posicion.get("latitudE6", 0) / 1000000
                        lon = posicion.get("longitudE6", 0) / 1000000

                        if lat and lon:
                            nombre = nodo.get("descripcion", {}).get("texto", "")
                            todas_paradas[codigo] = {
                                "codigo": codigo,
                                "nombre": nombre,
                                "latitud": lat,
                                "longitud": lon,
                                "calle": None,
                                "numero": None,
                            }
                except Exception as e:
                    fallos += 1
                    logger.warning(
                        f"Error fetching nodos for linea {linea_num} sentido {sentido}: {e}"
                    )
                    continue
                await asyncio.sleep(self.sync_request_delay_seconds)

        logger.info(
            "Total unique paradas found: %d (%d fallos de nodos)",
            len(todas_paradas), fallos,
        )
        # Si no se recuperó ninguna parada pese a haber líneas, el sync ha
        # fallado de facto: no machacamos el catálogo con una lista vacía.
        if not todas_paradas:
            raise RuntimeError(
                f"Sync de paradas sin resultados ({fallos} fallos de nodos); no se guarda nada"
            )
        # Aborta si la foto es sospechosamente parcial (sync en franja de baja
        # actividad), para no refrescar solo un subconjunto del catálogo.
        self._guard_completeness("paradas", len(todas_paradas), await db.count_paradas())
        await db.save_paradas_batch(list(todas_paradas.values()))
        return len(todas_paradas)

    async def get_all_paradas(self) -> List[dict]:
        """Obtiene todas las paradas de la base de datos."""
        return await db.get_all_paradas_from_db()

    def _calculate_bearing(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> float:
        """
        Calcula el rumbo (bearing) entre dos puntos usando la fórmula del
        ángulo azimutal sobre una esfera.

        El bearing indica hacia dónde está la parada respecto al usuario:
        - 0° = Norte, 90° = Este, 180° = Sur, 270° = Oeste

        Args:
            lat1, lon1: Punto de origen (ubicación del usuario)
            lat2, lon2: Punto de destino (parada)

        Returns:
            Rumbo en grados normalizado a [0, 360)
        """
        # Convertir grados a radianes
        lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
        dlon_r = math.radians(lon2 - lon1)

        # Componentes del vector de dirección
        x = math.sin(dlon_r) * math.cos(lat2_r)
        y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(
            lat2_r
        ) * math.cos(dlon_r)

        # atan2 devuelve [-180, 180], lo normalizamos a [0, 360)
        bearing = math.degrees(math.atan2(x, y))
        return (bearing + 360) % 360

    def _bearing_diff(self, bearing1: float, bearing2: float) -> float:
        """
        Calcula la diferencia mínima entre dos rumbos.

        Maneja el caso especial de 0° vs 360°.
        """
        diff = abs(bearing1 - bearing2)
        return min(diff, 360 - diff)

    async def get_paradas_cercanas(
        self,
        lat: float,
        lon: float,
        radio: int = 500,
        bearing: float = None,
        bearing_tolerance: float = 60,
    ) -> List[dict]:
        """
        Obtiene las paradas cercanas a una ubicación.

        Usa bounding box para pre-filtrar antes de aplicar Haversine,
        reduciendo el coste de O(n) a O(k) donde k << n.

        Args:
            lat, lon: Coordenadas del usuario
            radio: Radio de búsqueda en metros
            bearing: Orientación del usuario (0-360°)
            bearing_tolerance: Tolerancia en grados para filtrar por orientación

        Returns:
            Lista de paradas ordenadas por distancia (o bearing si se especifica)
        """
        all_paradas = await db.get_all_paradas_from_db()

        # Pre-filtrado por bounding box (descarta ~85% de paradas)
        lat_min, lat_max, lon_min, lon_max = db.bounding_box(lat, lon, radio * 1.1)

        cercanas = []
        for parada in all_paradas:
            plat, plon = parada["latitud"], parada["longitud"]

            # Filtro rápido de caja
            if not (lat_min <= plat <= lat_max and lon_min <= plon <= lon_max):
                continue

            # Haversine exacto sobre el subconjunto
            distancia = db.haversine(lat, lon, plat, plon)
            if distancia <= radio:
                parada_copy = parada.copy()
                parada_copy["distancia"] = round(distancia)

                if bearing is not None:
                    parada_bearing = self._calculate_bearing(lat, lon, plat, plon)
                    parada_copy["bearing"] = round(parada_bearing)
                    parada_copy["bearing_diff"] = round(
                        self._bearing_diff(bearing, parada_bearing)
                    )

                cercanas.append(parada_copy)

        if bearing is not None:
            return sorted(cercanas, key=lambda x: x.get("bearing_diff", 999))
        return sorted(cercanas, key=lambda x: x["distancia"])

    async def get_parada_by_codigo(self, codigo: str) -> Optional[dict]:
        """Obtiene una parada por su código."""
        return await db.get_parada_by_codigo(codigo)

    async def sync_lineas_from_api(self) -> int:
        """
        Sincroniza las líneas desde la API de TUSSAM.

        Returns:
            Número de líneas sincronizadas
        """
        fh = self._format_datetime()
        url = f"{self.base_url}/API/infotus-ui/lineas/{fh}"

        response = await self._get_with_retry(url)

        data = response.json()
        result_data = data.get("result", {})
        result = result_data.get("lineasDisponibles", [])

        if not result:
            raise RuntimeError(
                "TUSSAM no devolvió líneas disponibles; se aborta el sync de líneas"
            )

        lineas = []
        for item in result:
            destinos = item.get("destinos", [])
            # Primer destino = ida (sentido 1), segundo = vuelta (sentido 2)
            ida = next((d for d in destinos if d.get("sentido") == 1), {})
            vuelta = next((d for d in destinos if d.get("sentido") == 2), {})

            lineas.append({
                "numero": str(item.get("labelLinea", "")),
                "nombre": str(item.get("descripcion", {}).get("texto", "")),
                "color": str(item.get("color", "#000000")),
                "sublinea": item.get("sublinea"),
                "hora_inicio_ida": ida.get("horaInicio"),
                "hora_fin_ida": ida.get("horaFin"),
                "hora_inicio_vuelta": vuelta.get("horaInicio"),
                "hora_fin_vuelta": vuelta.get("horaFin"),
            })

        # Aborta si el origen devolvió muchas menos líneas de las conocidas
        # (franja de baja actividad), en vez de refrescar solo las activas.
        self._guard_completeness("líneas", len(lineas), await db.count_lineas())
        await db.save_lineas_batch(lineas)
        return len(lineas)

    async def _get_with_retry(self, url: str, max_retries: int = 3) -> httpx.Response:
        """
        GET con reintentos y backoff exponencial para manejar rate limits (429).

        Args:
            url: URL a la que hacer la petición
            max_retries: Número máximo de reintentos (default: 3)

        Returns:
            httpx.Response con la respuesta exitosa

        Raises:
            httpx.HTTPStatusError: Si falla tras todos los reintentos
        """
        retryable = {429, 500, 502, 503, 504}
        last_response = None
        for attempt in range(max_retries):
            async with self._tussam_semaphore:
                resp = await self.client.get(url)
            last_response = resp
            if resp.status_code in retryable:
                if attempt == max_retries - 1:
                    break
                wait = self._retry_wait_seconds(resp, attempt)
                logger.warning(
                    "HTTP %d de %s, reintentando en %ds (%d/%d)",
                    resp.status_code, url, wait, attempt + 1, max_retries,
                )
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        if last_response is None:
            raise httpx.HTTPError(f"No se intentó la petición a {url}")
        logger.error(
            "Reintentos agotados para %s (último: %d)",
            url,
            last_response.status_code,
        )
        last_response.raise_for_status()
        return last_response

    def _retry_wait_seconds(self, response: httpx.Response, attempt: int) -> int:
        """Respeta Retry-After cuando TUSSAM lo envía; si no, usa backoff."""
        headers = getattr(response, "headers", {}) or {}
        retry_after = headers.get("Retry-After") if hasattr(headers, "get") else None
        if isinstance(retry_after, str) and retry_after:
            try:
                return max(1, min(60, int(retry_after)))
            except ValueError:
                logger.debug("Retry-After no entero ignorado: %s", retry_after)
        return 2 ** (attempt + 1)

    async def sync_paradas_lineas_from_api(self) -> int:
        """
        Sincroniza la relación paradas ↔ líneas desde la API de TUSSAM.

        Recorre todas las líneas y sus nodos (sentidos 1 y 2) para construir
        la tabla de relación N:M.

        Returns:
            Número de relaciones sincronizadas
        """
        fh = self._format_datetime()

        # Obtener lista de líneas
        url_lineas = f"{self.base_url}/API/infotus-ui/lineas/{fh}"
        response = await self._get_with_retry(url_lineas)
        data = response.json()
        lineas = data.get("result", {}).get("lineasDisponibles", [])
        logger.info(f"Syncing paradas_lineas for {len(lineas)} lineas")

        if not lineas:
            raise RuntimeError(
                "TUSSAM no devolvió líneas disponibles; se aborta el sync de relaciones"
            )

        relaciones = []
        fallos = 0
        for linea in lineas:
            linea_num = linea.get("linea", 0)
            label = str(linea.get("labelLinea", ""))
            if not linea_num or not label:
                continue

            for sentido in [1, 2]:
                try:
                    url_nodos = f"{self.base_url}/API/infotus-ui/nodosLinea/{linea_num}/{sentido}/{fh}"
                    resp = await self._get_with_retry(url_nodos)
                    nodos = resp.json().get("result", [])

                    for orden, nodo in enumerate(nodos):
                        codigo = str(nodo.get("codigo", ""))
                        if codigo:
                            relaciones.append({
                                "parada_codigo": codigo,
                                "linea_numero": label,
                                "sentido": sentido,
                                "orden": orden,
                            })
                except Exception as e:
                    fallos += 1
                    logger.warning(f"Error nodos linea {label} sentido {sentido}: {e}")
                await asyncio.sleep(self.sync_request_delay_seconds)

        # save_paradas_lineas_batch hace DELETE global + reinserción. Si hubo
        # fallos, `relaciones` está incompleta y persistirla borraría las
        # relaciones de las líneas que fallaron. Abortamos para preservar el
        # último estado bueno; la tabla no se toca.
        if fallos:
            raise RuntimeError(
                f"Sync de relaciones incompleto: {fallos} fallos de nodos, "
                f"{len(relaciones)} relaciones parciales. No se reemplaza la tabla."
            )

        # Guardián de completitud: esta es la operación destructiva (borra y
        # reinserta). Si el origen listó pocas líneas activas (franja de baja
        # actividad), `relaciones` sería una fracción de la red y el DELETE la
        # dejaría mutilada. Abortamos si no alcanza el mínimo del catálogo actual.
        self._guard_completeness(
            "relaciones parada-línea", len(relaciones), await db.count_paradas_lineas()
        )

        await db.save_paradas_lineas_batch(relaciones)
        logger.info(f"Synced {len(relaciones)} parada-linea relations")
        return len(relaciones)

    async def get_lineas_de_parada(self, parada_codigo: str) -> List[str]:
        """Obtiene las líneas que pasan por una parada."""
        return await db.get_lineas_de_parada(parada_codigo)

    async def get_paradas_de_linea(self, linea_numero: str) -> List[dict]:
        """Obtiene las paradas de una línea."""
        return await db.get_paradas_de_linea(linea_numero)

    async def get_lineas(self) -> List[dict]:
        """Obtiene todas las líneas de la base de datos."""
        return await db.get_lineas_from_db()

    async def get_tiempos_parada(
        self, codigo_parada: str, force_refresh: bool = False
    ) -> dict:
        """
        Obtiene los tiempos de llegada para una parada.

        Args:
            codigo_parada: Código de la parada
            force_refresh: Si True, ignora el cache y obtiene datos frescos

        Returns:
            Dict con tiempos de llegada
        """
        if not force_refresh:
            cached = await db.get_cached_tiempos(codigo_parada)
            if cached:
                logger.info(f"Returning cached tiempos for parada {codigo_parada}")
                return cached

        lock = await self._get_tiempos_lock(codigo_parada)
        async with lock:
            if not force_refresh:
                cached = await db.get_cached_tiempos(codigo_parada)
                if cached:
                    logger.info(
                        "Returning cached tiempos after wait for parada %s",
                        codigo_parada,
                    )
                    return cached

            return await self._fetch_and_cache_tiempos(codigo_parada)

    async def _get_tiempos_lock(self, codigo_parada: str) -> asyncio.Lock:
        """Devuelve un lock estable por parada para deduplicar peticiones."""
        async with self._tiempos_locks_guard:
            return self._tiempos_locks.setdefault(codigo_parada, asyncio.Lock())

    async def _fetch_and_cache_tiempos(self, codigo_parada: str) -> dict:
        """Consulta TUSSAM para una parada y guarda la respuesta en cache."""
        url = f"{self.base_url}/API/infotus-ui/tiempos/{codigo_parada}"
        stale = await db.get_stale_cached_tiempos(codigo_parada)
        try:
            max_retries = 1 if stale else 3
            response = await self._get_with_retry(url, max_retries=max_retries)
            # Un JSONDecodeError (200 con HTML de Cloudflare, respuesta truncada)
            # no es un httpx.HTTPError, así que se captura aquí para poder
            # aplicar el mismo fallback stale que ante un error de red.
            data = response.json()
        except (httpx.HTTPError, httpx.TimeoutException, json.JSONDecodeError, ValueError):
            if stale:
                logger.warning(
                    "TUSSAM no disponible o respuesta inválida para parada %s; "
                    "devolviendo cache stale",
                    codigo_parada,
                )
                return stale
            raise

        result = self._normalize_tiempos_result(data.get("result"), codigo_parada)

        parada_info = result.get("descripcion", {}).get("texto", "")
        posicion = result.get("posicion", {})

        lineas = result.get("lineasCoincidentes", [])

        # Obtener sentidos de cada línea en esta parada
        sentidos_map = await db.get_sentidos_for_parada(codigo_parada)

        tiempos = []
        for linea in lineas:
            label = linea.get("labelLinea", "")
            color = linea.get("color", "#000000")
            estimaciones = linea.get("estimaciones", [])

            # Resolver sentido para esta línea en esta parada
            sentidos = sentidos_map.get(label, [])
            sentido = sentidos[0] if len(sentidos) == 1 else None

            for est in estimaciones:
                # Una estimación sin `segundos` no se puede interpretar. Si la
                # tratáramos como 0 minutos, el bucle la mostraría como "el bus
                # llega ya" y el orden por tiempo la pondría la primera,
                # desplazando estimaciones reales. Mejor descartarla.
                segundos = est.get("segundos")
                if segundos is None:
                    logger.debug(
                        "Estimación sin 'segundos' descartada para parada %s (linea %s)",
                        codigo_parada, label,
                    )
                    continue
                tiempo_min = segundos // 60
                destino = est.get("destino", {}).get("texto", "")

                tiempos.append({
                    "linea": label,
                    "color": color,
                    "tiempo_minutos": tiempo_min,
                    "destino": destino,
                    "distancia_metros": est.get("distancia"),
                    "vehiculo": est.get("vehiculo"),
                    "atributos": est.get("atributos", []),
                    "sentido": sentido,
                })

        # latitudE6/longitudE6 ausentes deben quedar como None, no como (0, 0)
        # —que situaría la parada en el golfo de Guinea—.
        lat_e6 = posicion.get("latitudE6") if posicion else None
        lon_e6 = posicion.get("longitudE6") if posicion else None
        result_data = {
            "parada": codigo_parada,
            "nombre": parada_info,
            "latitud": lat_e6 / 1000000 if lat_e6 else None,
            "longitud": lon_e6 / 1000000 if lon_e6 else None,
            "tiempos": sorted(tiempos, key=lambda x: x["tiempo_minutos"])[:10],
        }

        # Guardamos en cache. Si la normalización no produjo ni nombre ni
        # tiempos, el payload del origen era vacío o malformado: no lo cacheamos
        # para no envenenar la cache con una respuesta falsa "sin buses".
        if parada_info or result_data["tiempos"]:
            await db.save_tiempos_cache(codigo_parada, result_data)
        return result_data

    def _normalize_tiempos_result(self, result, codigo_parada: str) -> dict:
        """
        TUSSAM no documenta el contrato y a veces devuelve `result` como lista.

        Para la API pública, una respuesta vacía o malformada debe traducirse en
        "sin tiempos disponibles" en lugar de propagar un 500 al cliente.
        """
        if isinstance(result, dict):
            return result
        if isinstance(result, list):
            if not result:
                logger.info("TUSSAM sin tiempos para parada %s", codigo_parada)
                return {}
            first = result[0]
            if isinstance(first, dict):
                logger.warning(
                    "TUSSAM devolvió lista para parada %s; usando primer elemento",
                    codigo_parada,
                )
                return first
        logger.warning(
            "Payload inesperado de TUSSAM para parada %s: %s",
            codigo_parada,
            type(result).__name__,
        )
        return {}

    async def _geocode_nominatim_single(
        self, codigo: str, nombre: str, lat: float, lon: float
    ) -> tuple:
        """Geocodifica una parada con Nominatim. Usa nombre como fallback si no hay calle."""
        try:
            r = await self.client.get(
                NOMINATIM_API_URL,
                params={
                    "lat": lat, "lon": lon, "format": "json",
                    "addressdetails": 1, "zoom": 21, "layer": "address",
                },
                headers={"User-Agent": "TUSSAM-API/1.0"},
            )
            if r.status_code == 200:
                addr = r.json().get("address", {})
                calle = addr.get("road") or addr.get("footway") or addr.get("path") or ""
                numero = addr.get("house_number", "")
                cp = addr.get("postcode", "")
                municipio = addr.get("city") or addr.get("town") or addr.get("municipality", "")
                provincia = addr.get("county") or addr.get("state_district", "Sevilla")
                comunidad = addr.get("state", "")

                # Fallback: usar nombre de la parada si Nominatim no da calle.
                # Se registra explícitamente porque es un dato de menor calidad
                # que quedará fijado (get_paradas_sin_direccion no lo reprocesa).
                if not calle:
                    logger.info(
                        "Geocode %s sin calle en Nominatim; usando nombre de parada como fallback",
                        codigo,
                    )
                    calle = nombre
                    numero = ""

                if calle:
                    return (
                        codigo, calle, numero, cp, municipio, provincia,
                        comunidad, f"{calle} {numero}".strip(),
                    )
            else:
                # Un 403/429 de Nominatim (política de User-Agent, rate limit)
                # pasaría inadvertido sin este log, y toda la geocodificación
                # fallaría en silencio.
                logger.warning(
                    "Geocode %s: Nominatim devolvió HTTP %d", codigo, r.status_code
                )
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            logger.warning("Geocode %s: error de red con Nominatim: %s", codigo, e)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Geocode %s: respuesta no parseable de Nominatim: %s", codigo, e)
        except Exception:
            logger.exception("Geocode %s: error inesperado", codigo)
        return (codigo, None, None, None, None, None, None, None)

    async def sync_direcciones_all(self) -> dict:
        """
        Geocodifica paradas sin dirección usando Nominatim.

        Procesa secuencialmente a 1 req/s (límite de Nominatim).
        Solo procesa paradas que no tienen calle asignada.

        Returns:
            Dict con estadísticas del proceso
        """
        paradas = await db.get_paradas_sin_direccion()

        if not paradas:
            logger.info("All paradas already have addresses")
            return {"total": 0, "ok": 0, "errors": 0}

        logger.info(f"Geocoding {len(paradas)} paradas with Nominatim")

        ok = errors = 0
        for i, p in enumerate(paradas):
            codigo, calle, numero, cp, muni, prov, ccaa, completa = (
                await self._geocode_nominatim_single(
                    p["codigo"], p.get("nombre", ""), p["latitud"], p["longitud"]
                )
            )

            if calle:
                await db.update_parada_direccion(
                    codigo, calle, numero, cp, muni, prov, ccaa, completa
                )
                ok += 1
            else:
                errors += 1

            if (i + 1) % 10 == 0:
                logger.info(f"Geocoded {i + 1}/{len(paradas)} paradas")

            # Rate limit Nominatim: 1 req/s
            if i + 1 < len(paradas):
                await asyncio.sleep(1.1)

        stats = {"total": len(paradas), "ok": ok, "errors": errors}
        logger.info(f"Geocoding complete: {stats}")
        return stats


# Instancia singleton del servicio
tussam_service = TussamService()
