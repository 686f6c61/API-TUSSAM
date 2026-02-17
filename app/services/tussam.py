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
Versión: 1.0.0
Licencia: MIT
"""

import httpx
from datetime import datetime
from typing import List, Optional
import app.database as db
import logging
import asyncio
import math

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# URLs de las APIs externas
BASE_URL = "https://reddelineas.tussam.es"
NOMINATIM_API_URL = "https://nominatim.openstreetmap.org/reverse"
PHOTON_REVERSE_URL = "https://photon.komoot.io/reverse"


class TussamService:
    """
    Servicio principal para interactuar con la API de TUSSAM.

    Maneja:
    - Peticiones a la API de TUSSAM (tiempos, paradas, líneas)
    - Geocodificación con Nominatim (OpenStreetMap)
    - Cacheo de resultados
    """

    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
                "Accept": "application/json",
                "Referer": "https://reddelineas.tussam.es/",
            },
        )
        self.base_url = BASE_URL

    async def close(self):
        """Cierra el cliente HTTP al apagar la aplicación."""
        await self.client.aclose()

    async def get_direccion_from_coords(self, lat: float, lon: float) -> dict:
        """
        Obtiene la dirección (calle, número, código postal) a partir de coordenadas.

        Usa Nominatim (OpenStreetMap) para geocodificación inversa.
        Los resultados se cachean en SQLite para evitar repetir peticiones.

        Args:
            lat: Latitud
            lon: Longitud

        Returns:
            Dict con: calle, numero, codigo_postal, municipio, provincia, direccion_completa
        """
        # Primero miramos si está en cache
        cached = await db.get_cached_direccion(lat, lon)
        if cached:
            logger.info(f"Returning cached direccion for {lat}, {lon}")
            return cached

        try:
            # Petición a Nominatim
            params = {
                "lat": lat,
                "lon": lon,
                "format": "json",
                "addressdetails": 1,
                "zoom": 18,
            }
            headers = {"User-Agent": "TUSSAM-API/1.0"}
            response = await self.client.get(
                NOMINATIM_API_URL, params=params, headers=headers
            )

            if response.status_code != 200:
                logger.warning("Nominatim HTTP %d para coords (%f, %f)", response.status_code, lat, lon)
            else:
                data = response.json()
                address = data.get("address", {})

                # Extraemos los datos relevantes
                tipo = (
                    address.get("road")
                    or address.get("footway")
                    or address.get("path")
                    or ""
                )
                numero = address.get("house_number") or ""

                direccion = {
                    "calle": tipo,
                    "numero": numero,
                    "codigo_postal": address.get("postcode", ""),
                    "municipio": address.get("city")
                    or address.get("town")
                    or address.get("municipality", ""),
                    "provincia": address.get("county")
                    or address.get("province", "Sevilla"),
                    "tipo_via": "",
                }

                # Construimos la dirección completa
                if direccion["calle"]:
                    if direccion["numero"]:
                        direccion["direccion_completa"] = (
                            f"{direccion['calle']} {direccion['numero']}"
                        )
                    else:
                        direccion["direccion_completa"] = direccion["calle"]
                else:
                    direccion["direccion_completa"] = ""

                # Guardamos en cache
                await db.save_direccion_cache(lat, lon, direccion)
                return direccion
        except Exception as e:
            logger.warning(f"Error getting direccion from coords: {e}")

        # Si falla, devolvemos valores por defecto
        return {
            "calle": "",
            "numero": "",
            "codigo_postal": "",
            "municipio": "Sevilla",
            "provincia": "Sevilla",
            "tipo_via": "",
            "direccion_completa": "",
        }

    def _format_datetime(self, dt: Optional[datetime] = None) -> str:
        """
        Formatea la fecha para la API de TUSSAM.

        Formato: DD-MM-AAAATHH:MM:SS (con : encodeado como %3A)
        """
        if dt is None:
            dt = datetime.now()
        return dt.strftime("%d-%m-%YT%H:%M:%S").replace(":", "%3A").replace("/", "-")

    def _coords_to_tussam(self, lat: float, lon: float) -> tuple:
        """
        Convierte coordenadas a formato de TUSSAM (multiplicado por 10^6).
        """
        lat_int = int(lat * 1000000)
        lon_int = int(lon * 1000000)
        return lat_int, lon_int

    async def sync_paradas_from_api(self) -> int:
        """
        Sincroniza las paradas desde la API de TUSSAM.

        Itera por todas las líneas y sus nodos para obtener todas las paradas.
        Guarda los resultados en la base de datos SQLite.

        Returns:
            Número de paradas sincronizadas
        """
        logger.info("Fetching lineas from TUSSAM API")
        fh = self._format_datetime()

        # Obtenemos la lista de líneas
        url_lineas = f"{self.base_url}/API/infotus-ui/lineas/{fh}"
        response = await self.client.get(url_lineas)
        response.raise_for_status()

        data_lineas = response.json()
        lineas_data = data_lineas.get("result", {})
        lineas = lineas_data.get("lineasDisponibles", [])
        logger.info(f"Found {len(lineas)} lineas")

        todas_paradas: dict = {}

        # Por cada línea, obtenemos los nodos (paradas) en ambos sentidos
        for linea in lineas:
            linea_num = linea.get("linea", 0)
            if not linea_num:
                continue

            for sentido in [1, 2]:
                try:
                    url_nodos = f"{self.base_url}/API/infotus-ui/nodosLinea/{linea_num}/{sentido}/{fh}"
                    resp = await self.client.get(url_nodos)
                    resp.raise_for_status()
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
                    logger.warning(
                        f"Error fetching nodos for linea {linea_num} sentido {sentido}: {e}"
                    )
                    continue

        logger.info(f"Total unique paradas found: {len(todas_paradas)}")
        await db.save_paradas_batch(list(todas_paradas.values()))
        return len(todas_paradas)

    async def get_all_paradas(self) -> List[dict]:
        """Obtiene todas las paradas de la base de datos."""
        return await db.get_all_paradas_from_db()

    def _calculate_bearing(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> float:
        """
        Calcula el rumbo (bearing) entre dos puntos.

        Args:
            lat1, lon1: Punto de origen
            lat2, lon2: Punto de destino

        Returns:
            Rumbo en grados (0-360)
        """
        lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
        dlon_r = math.radians(lon2 - lon1)

        x = math.sin(dlon_r) * math.cos(lat2_r)
        y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(
            lat2_r
        ) * math.cos(dlon_r)

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

        Args:
            lat, lon: Coordenadas del usuario
            radio: Radio de búsqueda en metros
            bearing: Orientación del usuario (0-360°)
            bearing_tolerance: Tolerancia en grados para filtrar por orientación

        Returns:
            Lista de paradas ordenadas por distancia (o bearing si se especifica)
        """
        all_paradas = await db.get_all_paradas_from_db()

        cercanas = []
        for parada in all_paradas:
            distancia = db.haversine(lat, lon, parada["latitud"], parada["longitud"])
            if distancia <= radio:
                parada_copy = parada.copy()
                parada_copy["distancia"] = round(distancia)

                # Si hay bearing, calculamos la diferencia
                if bearing is not None:
                    parada_bearing = self._calculate_bearing(
                        lat, lon, parada["latitud"], parada["longitud"]
                    )
                    parada_copy["bearing"] = round(parada_bearing)
                    parada_copy["bearing_diff"] = round(
                        self._bearing_diff(bearing, parada_bearing)
                    )

                cercanas.append(parada_copy)

        # Ordenamos por distancia o por diferencia de bearing
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

        response = await self.client.get(url)
        response.raise_for_status()

        data = response.json()
        result_data = data.get("result", {})
        result = result_data.get("lineasDisponibles", [])

        lineas = [
            {
                "numero": str(item.get("labelLinea", "")),
                "nombre": str(item.get("descripcion", {}).get("texto", "")),
                "color": str(item.get("color", "#000000")),
            }
            for item in result
        ]

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
        retryable = {429, 500, 502, 503}
        for attempt in range(max_retries):
            resp = await self.client.get(url)
            if resp.status_code in retryable:
                wait = 2 ** (attempt + 1)
                logger.warning(
                    "HTTP %d de %s, reintentando en %ds (%d/%d)",
                    resp.status_code, url, wait, attempt + 1, max_retries,
                )
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        logger.error("Reintentos agotados para %s (último: %d)", url, resp.status_code)
        resp.raise_for_status()
        return resp

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

        relaciones = []
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
                    logger.warning(f"Error nodos linea {label} sentido {sentido}: {e}")
                await asyncio.sleep(0.5)

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

        url = f"{self.base_url}/API/infotus-ui/tiempos/{codigo_parada}"
        response = await self._get_with_retry(url)

        data = response.json()
        result = data.get("result", {})

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
                tiempo_min = est.get("segundos", 0) // 60
                destino = est.get("destino", {}).get("texto", "")

                tiempos.append(
                    {
                        "linea": label,
                        "color": color,
                        "tiempo_minutos": tiempo_min,
                        "destino": destino,
                        "distancia_metros": est.get("distancia", 0),
                        "sentido": sentido,
                    }
                )

        result_data = {
            "parada": codigo_parada,
            "nombre": parada_info,
            "latitud": posicion.get("latitudE6", 0) / 1000000 if posicion else None,
            "longitud": posicion.get("longitudE6", 0) / 1000000 if posicion else None,
            "tiempos": sorted(tiempos, key=lambda x: x["tiempo_minutos"])[:10],
        }

        # Guardamos en cache
        await db.save_tiempos_cache(codigo_parada, result_data)
        return result_data

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

                # Fallback: usar nombre de la parada si no hay calle
                if not calle:
                    calle = nombre
                    numero = ""

                if calle:
                    return (
                        codigo, calle, numero, cp, municipio, provincia,
                        comunidad, f"{calle} {numero}".strip(),
                    )
        except Exception as e:
            logger.warning(f"Geocode error {codigo}: {e}")
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
