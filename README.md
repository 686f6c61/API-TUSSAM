# TUSSAM API

API REST para datos en tiempo real de autobuses TUSSAM (Sevilla). Diseñada para alimentar una app de Apple Watch que muestra paradas cercanas y tiempos de llegada.

## Stack

- **FastAPI** + **uvicorn** (async)
- **SQLite** + **aiosqlite** (datos + cache)
- **httpx** (cliente HTTP async)
- **APScheduler** (sincronización semanal automática)
- **Docker** para despliegue

## Inicio rápido

```bash
# Opción 1: Con Docker (recomendado)
docker compose up -d
curl http://localhost:8081/health

# Opción 2: Sin Docker
pip install -e .
uvicorn app.main:app --reload --port 8080
```

La base de datos incluida (`data/tussam.db`) ya tiene todos los datos. No hace falta ejecutar ningún sync.

> Guía completa de Docker con variables de entorno, persistencia, troubleshooting y más en [docs/docker.md](docs/docker.md).

## Endpoints principales

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/cercanas?lat=...&lon=...` | Paradas cercanas con tiempos en tiempo real |
| GET | `/paradas` | Listado de todas las paradas |
| GET | `/paradas/cercanas?lat=...&lon=...` | Paradas cercanas (sin tiempos) |
| GET | `/paradas/{codigo}` | Detalle de una parada |
| GET | `/paradas/{codigo}/tiempos` | Tiempos de llegada en tiempo real |
| GET | `/paradas/{codigo}/lineas` | Líneas que pasan por una parada |
| GET | `/lineas` | Listado de todas las líneas |
| GET | `/lineas/{numero}/paradas` | Paradas de una línea |
| GET | `/health` | Estado del servicio |

Endpoints de sincronización (requieren `X-API-Key`):

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/sync/all` | Sync completo (paradas + líneas + relaciones) |
| POST | `/sync/paradas` | Sync solo paradas |
| POST | `/sync/lineas` | Sync solo líneas |
| POST | `/sync/paradas-lineas` | Sync relaciones parada-línea |
| POST | `/sync/direcciones` | Geocodificación de direcciones |

Documentación completa de la API con ejemplos en [docs/API.md](docs/API.md). Despliegue con Docker en [docs/docker.md](docs/docker.md).

## Datos incluidos (SQLite)

El repositorio incluye `data/tussam.db` con datos pre-cargados para uso inmediato.

### Snapshot de datos: 16 de febrero de 2026

| Tabla | Registros | Descripción |
|-------|-----------|-------------|
| `paradas` | 967 | Paradas de autobús de Sevilla (código, nombre, GPS, dirección) |
| `lineas` | 43 | Líneas de autobús (número, nombre, color) |
| `paradas_lineas` | 1,756 | Relaciones N:M entre paradas y líneas (sentido + orden) |
| `tiempos_cache` | efímero | Cache de tiempos de llegada (TTL: 1 minuto) |
| `direcciones_cache` | efímero | Cache de geocodificación inversa (TTL: 30 días) |

**Cobertura de direcciones geocodificadas:**

| Campo | Paradas | % |
|-------|---------|---|
| calle | 967/967 | 100% |
| numero | 808/967 | 83.6% |
| codigo_postal | 967/967 | 100% |
| municipio | 967/967 | 100% |
| provincia | 967/967 | 100% |
| comunidad_autonoma | 967/967 | 100% |
| direccion_completa | 967/967 | 100% |

> `numero` no llega al 100% porque muchas paradas no tienen un portal asociado (glorietas, avenidas, puentes).

Datos sincronizados desde la API de TUSSAM (`reddelineas.tussam.es`) el 15-16 de febrero de 2026. Las direcciones se obtuvieron por geocodificación inversa con Nominatim (OpenStreetMap).

> Para actualizar los datos: `POST /sync/all` o esperar al scheduler semanal (domingos 04:00 UTC por defecto).

### Geocodificación de coordenadas

Cada parada de TUSSAM tiene coordenadas GPS (latitud/longitud) pero no dirección postal. El sistema convierte esas coordenadas en calle, número, código postal, etc. mediante **geocodificación inversa** con [Nominatim](https://nominatim.openstreetmap.org/) (API gratuita de OpenStreetMap).

**Flujo:**

```
Coordenadas GPS (37.3886, -5.9822)
        │
        ▼
Nominatim reverse geocoding
GET https://nominatim.openstreetmap.org/reverse?lat=37.3886&lon=-5.9822&format=json&addressdetails=1&zoom=21&layer=address
        │
        ▼
Respuesta JSON con: road, house_number, postcode, city, state...
        │
        ▼
Se guarda en tabla `paradas`: calle, numero, codigo_postal, municipio, provincia, comunidad_autonoma, direccion_completa
```

**Fallback:** si Nominatim no devuelve calle (zonas sin cartografiar), se usa el nombre de la parada como valor de `calle`. Esto garantiza que `calle` y `direccion_completa` siempre tengan valor.

**Ejecutar la geocodificación manualmente:**

```bash
# Opción 1: Con la API arrancada (requiere X-API-Key si está configurada)
curl -X POST http://localhost:8080/sync/direcciones \
  -H "X-API-Key: tu-clave"

# Opción 2: Script directo con Python (sin necesidad de servidor)
python3 -c "
import asyncio
from app.services.tussam import tussam_service
from app import database

async def main():
    await database.init_db()
    result = await tussam_service.sync_direcciones_all()
    print(f'Total: {result[\"total\"]}, OK: {result[\"ok\"]}, Errores: {result[\"errors\"]}')
    await tussam_service.close()

asyncio.run(main())
"
```

> Nominatim tiene un rate limit de 1 petición/segundo. Geocodificar las 967 paradas tarda ~17 minutos. Solo se procesan paradas sin `calle` asignada, así que las siguientes ejecuciones son instantáneas si no hay paradas nuevas.

### Esquema de la base de datos

```
paradas (967 registros)
├── codigo TEXT PRIMARY KEY        -- Código único de la parada
├── nombre TEXT NOT NULL           -- Nombre descriptivo
├── latitud REAL NOT NULL          -- Coordenada GPS
├── longitud REAL NOT NULL         -- Coordenada GPS
├── calle TEXT                     -- Calle (geocodificada)
├── numero TEXT                    -- Número de calle
├── codigo_postal TEXT             -- CP (geocodificado)
├── municipio TEXT                 -- Municipio (geocodificado)
├── provincia TEXT                 -- Provincia (geocodificado)
├── comunidad_autonoma TEXT        -- CCAA (geocodificado)
├── direccion_completa TEXT        -- "Calle Número" formateado
└── updated_at TIMESTAMP           -- Última actualización

lineas (43 registros)
├── numero TEXT PRIMARY KEY        -- Número de línea (01, C1, etc.)
├── nombre TEXT NOT NULL           -- Nombre/descripción de la línea
├── color TEXT NOT NULL            -- Color hex (#f54129)
└── updated_at TIMESTAMP           -- Última actualización

paradas_lineas (1,756 relaciones)
├── parada_codigo TEXT NOT NULL    -- FK → paradas.codigo
├── linea_numero TEXT NOT NULL     -- FK → lineas.numero
├── sentido INTEGER NOT NULL       -- 1 (ida) o 2 (vuelta)
├── orden INTEGER NOT NULL         -- Posición en el recorrido
└── PRIMARY KEY (parada_codigo, linea_numero, sentido)

tiempos_cache (efímero, TTL 1 min)
├── parada_codigo TEXT PRIMARY KEY
├── tiempos_json TEXT NOT NULL
└── cached_at TIMESTAMP NOT NULL

direcciones_cache (efímero, TTL 30 días)
├── latitud REAL NOT NULL
├── longitud REAL NOT NULL
├── direccion_json TEXT NOT NULL
├── cached_at TIMESTAMP NOT NULL
└── PRIMARY KEY (latitud, longitud)
```

## Variables de entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `SYNC_API_KEY` | *(vacío)* | API key para endpoints `/sync/*` |
| `SYNC_ENABLED` | `true` | Activar scheduler de sync semanal |
| `SYNC_DAY` | `sun` | Día de sync (mon, tue, wed, ...) |
| `SYNC_HOUR` | `4` | Hora UTC del sync |
| `SYNC_MINUTE` | `0` | Minuto del sync |

## Tests

```bash
# Instalar dependencias de desarrollo
pip install -e ".[dev]"

# Tests unitarios (91 tests, ~1s)
python -m pytest tests/ --ignore=tests/test_e2e.py -v

# Tests end-to-end con API real de TUSSAM (30 tests, ~1min)
python -m pytest tests/test_e2e.py -v -s
```

## Seguridad

- Autenticación por API key (timing-safe con `hmac.compare_digest`)
- Rate limiting dual: por dispositivo (`X-Device-ID`, 60/min) + por IP (300/min)
- Validación de parámetros con bounds (lat, lon, radio, bearing, sentido)
- SQLite en modo WAL con `busy_timeout=5000`

## Licencia

MIT

## Autor

[686f6c61](https://github.com/686f6c61)
