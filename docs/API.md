# TUSSAM API - Documentacion para Desarrolladores

API REST de codigo abierto para datos en tiempo real de autobuses TUSSAM (Transportes Urbanos de Sevilla). Optimizada para Apple Watch y apps moviles.

- Datos publicos: paradas, lineas, tiempos de llegada en tiempo real
- Geocodificacion: cada parada tiene calle, numero, codigo postal
- Cache inteligente: tiempos se cachean 1 minuto, direcciones 30 dias
- Documentacion interactiva opcional por entorno (`ENABLE_API_DOCS=true`)

---

## Indice

- [Despliegue](#despliegue)
- [Autenticacion](#autenticacion)
- [Rate Limiting](#rate-limiting)
- [Referencia de Endpoints](#referencia-de-endpoints)
  - [GET /cercanas](#get-cercanas) (endpoint principal)
  - [GET /paradas](#get-paradas)
  - [GET /paradas/cercanas](#get-paradascercanas)
  - [GET /paradas/{codigo}](#get-paradascodigo)
  - [GET /paradas/{codigo}/tiempos](#get-paradascodigotiempos)
  - [GET /paradas/{codigo}/lineas](#get-paradascodigolineas)
  - [GET /lineas](#get-lineas)
  - [GET /lineas/{numero}/paradas](#get-lineasnumeroparadas)
  - [GET /health](#get-health)
  - [POST /sync/*](#endpoints-de-sincronizacion)
- [Filtros Avanzados](#filtros-avanzados)
- [Formatos de Respuesta](#formatos-de-respuesta)
- [Codigos de Error](#codigos-de-error)
- [Ejemplos Completos](#ejemplos-completos)
- [Sincronizacion Automatica](#sincronizacion-automatica)
- [Arquitectura](#arquitectura)

---

## Despliegue

### Con Docker (recomendado)

```bash
# 1. Clonar el repositorio
git clone https://github.com/686f6c61/API-TUSSAM.git
cd API-TUSSAM

# 2. Configurar la API key para endpoints de administracion
export SYNC_API_KEY=$(openssl rand -hex 32)
echo "SYNC_API_KEY=$SYNC_API_KEY"   # guardar esta clave

# 3. Arrancar el contenedor
docker compose up -d

# 4. Verificar que funciona
curl http://localhost:8081/health
```

El contenedor expone el puerto **8081** (configurable en `docker-compose.yml`).

La base de datos SQLite incluida (`data/tussam.db`) ya contiene 967 paradas, 43 lineas y 1,756 relaciones. No es necesario ejecutar sync la primera vez.

**docker-compose.yml** de referencia:

```yaml
version: '3.8'

services:
  tussam:
    build: .
    container_name: tussam-api
    ports:
      - "8081:8080"
    volumes:
      - ./data:/app/data          # persistir la DB fuera del contenedor
    environment:
      - SYNC_ENABLED=true          # scheduler semanal activo
      - SYNC_DAY=sun               # sincronizar los domingos
      - SYNC_HOUR=4                # a las 04:00 UTC
      - SYNC_MINUTE=0
      - SYNC_API_KEY=${SYNC_API_KEY:-}
      - ALLOW_INSECURE_SYNC=false
      - ENABLE_API_DOCS=false
      - CORS_ALLOW_ORIGINS=${CORS_ALLOW_ORIGINS:-}
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]
      interval: 30s
      timeout: 5s
      retries: 3
    restart: unless-stopped
```

### Sin Docker

```bash
# Instalar dependencias
pip install -e .

# Arrancar servidor de desarrollo
uvicorn app.main:app --reload --port 8080

# Arrancar en produccion
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

### Verificar el despliegue

```bash
# Health check
curl http://localhost:8080/health
# {"status":"ok","paradas_en_db":967}

# Paradas cercanas a Puerta de Carmona
curl "http://localhost:8080/cercanas?lat=37.3886&lon=-5.9850&max_paradas=1"
```

---

## Autenticacion

La API tiene dos niveles de acceso:

| Tipo | Endpoints | Autenticacion |
|------|-----------|---------------|
| **Lectura** | Todos los `GET` | Ninguna (publico) |
| **Administracion** | `POST /sync/*` | Header `X-API-Key` obligatorio |

```bash
# Endpoints publicos: sin autenticacion
curl http://localhost:8080/paradas

# Endpoints de sync: requieren X-API-Key
curl -X POST http://localhost:8080/sync/all \
  -H "X-API-Key: tu-clave-secreta"
```

La API key se configura con la variable de entorno `SYNC_API_KEY`. Si no se configura, los endpoints `POST /sync/*` quedan bloqueados con `503` (fail-closed). Para desarrollo local se puede habilitar `ALLOW_INSECURE_SYNC=true`. La comparacion de claves usa `hmac.compare_digest` para prevenir timing attacks.

---

## Rate Limiting

Dos niveles de limitacion:

| Identificacion | Limite | Cuando se usa |
|----------------|--------|---------------|
| Header `X-Device-ID` | **60 req/min** | Si el cliente envia el header (max 64 caracteres) |
| IP del cliente | **300 req/min** | Fallback si no hay `X-Device-ID` |

El limite por IP es generoso porque muchos dispositivos pueden compartir IP (CGNAT, iCloud Private Relay, WiFi compartida).

**Para apps Watch/movil**: enviar siempre `X-Device-ID` con un UUID unico generado al instalar la app:

```
X-Device-ID: 550e8400-e29b-41d4-a716-446655440000
```

Cuando se excede el limite, la API devuelve:

```
HTTP 429 Too Many Requests
Retry-After: 60

{"detail": "Demasiadas peticiones. Maximo 60/min."}
```

---

## Referencia de Endpoints

### `GET /cercanas`

**Endpoint principal.** Devuelve las paradas mas cercanas CON tiempos de llegada en una sola llamada. Disenado para Apple Watch y apps moviles.

**Parametros:**

| Parametro | Tipo | Obligatorio | Default | Rango | Descripcion |
|-----------|------|:-----------:|---------|-------|-------------|
| `lat` | float | **Si** | — | -90 a 90 | Latitud del usuario |
| `lon` | float | **Si** | — | -180 a 180 | Longitud del usuario |
| `radio` | int | No | 300 | 50 - 2000 | Radio de busqueda en metros |
| `max_paradas` | int | No | 3 | 1 - 10 | Maximo de paradas a devolver |
| `bearing` | float | No | null | 0 - 360 | Orientacion del usuario en grados |
| `bearing_tolerance` | float | No | 60 | 0 - 180 | Tolerancia de orientacion en grados |
| `tiempo_max` | int | No | null | >= 0 | Solo buses que lleguen en maximo X minutos |
| `lineas` | string | No | null | — | Filtrar lineas separadas por comas (ej: `01,C4`) |
| `sentido` | int | No | null | 1 o 2 | Filtrar por sentido/direccion |
| `formato` | string | No | `json` | json, geojson | Formato de respuesta |
| `incluir_mapa` | bool | No | false | — | Incluir URL de OpenStreetMap |

**Ejemplo:**

```bash
curl "http://localhost:8080/cercanas?lat=37.3891&lon=-5.9845&max_paradas=2&bearing=180"
```

**Respuesta:**

```json
{
  "ubicacion": {
    "lat": 37.3891,
    "lon": -5.9845,
    "bearing": 180
  },
  "paradas": [
    {
      "codigo": "889",
      "nombre": "Recaredo (San Roque)",
      "latitud": 37.391250,
      "longitud": -5.984236,
      "distancia": 66,
      "bearing": 168,
      "bearing_diff": 12,
      "calle": "Calle Recaredo",
      "direccion_completa": "Calle Recaredo 5",
      "tiempos": [
        {
          "linea": "01",
          "color": "#f54129",
          "tiempo_minutos": 4,
          "destino": "HOSPITAL V.ROCIO",
          "distancia_metros": 783,
          "sentido": 1
        },
        {
          "linea": "C3",
          "color": "#008431",
          "tiempo_minutos": 7,
          "destino": "PRADO",
          "distancia_metros": 1200,
          "sentido": null
        }
      ]
    }
  ]
}
```

**Campos de la respuesta:**

| Campo | Tipo | Descripcion |
|-------|------|-------------|
| `codigo` | string | Codigo unico de la parada |
| `nombre` | string | Nombre descriptivo |
| `latitud`, `longitud` | float | Coordenadas GPS |
| `distancia` | int | Metros desde la ubicacion del usuario |
| `bearing` | int / null | Rumbo hacia la parada (solo si se envia `bearing`) |
| `bearing_diff` | int / null | Diferencia angular con el bearing del usuario |
| `calle` | string | Calle de la parada (vacio si no geocodificada) |
| `direccion_completa` | string | "Calle Numero" formateado |
| `tiempos` | array | Buses en camino (maximo **5** por parada) |
| `mapa_url` | string / ausente | URL de OpenStreetMap (solo si `incluir_mapa=true`) |

**Campos de cada tiempo:**

| Campo | Tipo | Descripcion |
|-------|------|-------------|
| `linea` | string | Numero de linea (ej: `"01"`, `"C4"`) |
| `color` | string | Color hex de la linea |
| `tiempo_minutos` | int | Minutos hasta la llegada. Puede ser **negativo** (-30 = bus en la parada) |
| `destino` | string | Nombre del destino final |
| `distancia_metros` | int | Distancia del bus a la parada |
| `sentido` | int / null | `1` (ida), `2` (vuelta), `null` si la parada tiene esa linea en ambos sentidos |

**Comportamiento importante:**

- Si TUSSAM esta caida, `/cercanas` **siempre devuelve 200**. Las paradas aparecen con `"tiempos": []`.
- Los tiempos se cachean **1 minuto**. Peticiones repetidas en ese intervalo no golpean la API de TUSSAM.
- Los campos `numero`, `codigo_postal`, `municipio`, `provincia`, `comunidad_autonoma` y `updated_at` **no se incluyen** en `/cercanas`. Para obtenerlos, usar `GET /paradas/{codigo}`.
- El filtro `lineas` convierte automaticamente a mayusculas (`c4` → `C4`).
- El filtro `tiempo_max` excluye tiempos negativos (buses ya en la parada).

**Errores:**

| Codigo | Cuando |
|--------|--------|
| 400 | `lat` fuera de [-90, 90] |
| 400 | `lon` fuera de [-180, 180] |
| 400 | `bearing` fuera de [0, 360] |
| 400 | `formato` distinto de `json` o `geojson` |
| 400 | `sentido` distinto de 1 o 2 |
| 422 | `radio` fuera de [50, 2000] |
| 422 | `max_paradas` fuera de [1, 10] |
| 422 | `bearing_tolerance` fuera de [0, 180] |
| 422 | `tiempo_max` negativo |

---

### `GET /paradas`

Todas las paradas de TUSSAM con todos sus campos, incluyendo direccion completa.

```bash
curl http://localhost:8080/paradas
```

```json
[
  {
    "codigo": "889",
    "nombre": "Recaredo (San Roque)",
    "latitud": 37.391250,
    "longitud": -5.984236,
    "calle": "Calle Recaredo",
    "numero": "5",
    "codigo_postal": "41003",
    "municipio": "Sevilla",
    "provincia": "Sevilla",
    "comunidad_autonoma": "Andalucia",
    "direccion_completa": "Calle Recaredo 5",
    "updated_at": "2026-02-16 20:41:18"
  }
]
```

Ordenadas por `codigo`. Actualmente devuelve 967 paradas.

---

### `GET /paradas/cercanas`

Paradas cercanas **sin tiempos de llegada**. Mas rapido que `/cercanas`. Util para marcadores en un mapa.

**Parametros:**

| Parametro | Tipo | Obligatorio | Default | Rango | Descripcion |
|-----------|------|:-----------:|---------|-------|-------------|
| `lat` | float | **Si** | — | -90 a 90 | Latitud |
| `lon` | float | **Si** | — | -180 a 180 | Longitud |
| `radio` | int | No | 500 | 50 - 2000 | Radio en metros |
| `bearing` | float | No | null | 0 - 360 | Orientacion del usuario |
| `bearing_tolerance` | float | No | 60 | 0 - 180 | Tolerancia en grados |

> **Nota:** `radio` aqui vale 500 por defecto (vs 300 en `/cercanas`).

```bash
curl "http://localhost:8080/paradas/cercanas?lat=37.3997&lon=-5.9838&radio=200"
```

```json
[
  {
    "codigo": "20",
    "nombre": "Ronda de Capuchinos (San Julian)",
    "latitud": 37.399726,
    "longitud": -5.983828,
    "calle": "Ronda de Capuchinos",
    "numero": "2",
    "distancia": 5,
    "bearing": 145,
    "bearing_diff": 35
  }
]
```

Devuelve todos los campos de la parada mas `distancia`, `bearing` y `bearing_diff`. No tiene limite de resultados (devuelve todas las paradas dentro del radio). No incluye tiempos.

**Errores:** mismos que `/cercanas` para lat, lon, bearing, radio, bearing_tolerance.

---

### `GET /paradas/{codigo}`

Datos completos de una parada por su codigo.

```bash
curl http://localhost:8080/paradas/889
```

```json
{
  "codigo": "889",
  "nombre": "Recaredo (San Roque)",
  "latitud": 37.391250,
  "longitud": -5.984236,
  "calle": "Calle Recaredo",
  "numero": "5",
  "codigo_postal": "41003",
  "municipio": "Sevilla",
  "provincia": "Sevilla",
  "comunidad_autonoma": "Andalucia",
  "direccion_completa": "Calle Recaredo 5",
  "updated_at": "2026-02-16 20:41:18"
}
```

**Errores:**

| Codigo | Cuando |
|--------|--------|
| 404 | Parada con ese codigo no existe |

---

### `GET /paradas/{codigo}/tiempos`

Tiempos de llegada en tiempo real para una parada. Los tiempos se obtienen de la API de TUSSAM y se cachean durante 1 minuto.

```bash
curl http://localhost:8080/paradas/889/tiempos
```

```json
{
  "parada": "889",
  "nombre": "Recaredo (San Roque)",
  "latitud": 37.391250,
  "longitud": -5.984236,
  "tiempos": [
    {
      "linea": "27",
      "color": "#8B4513",
      "tiempo_minutos": 3,
      "destino": "SEVILLA ESTE",
      "distancia_metros": 560,
      "sentido": 1
    },
    {
      "linea": "C3",
      "color": "#008431",
      "tiempo_minutos": 5,
      "destino": "PRADO",
      "distancia_metros": 980,
      "sentido": null
    }
  ]
}
```

- Maximo **10** tiempos por parada, ordenados por `tiempo_minutos` ascendente.
- `tiempo_minutos` puede ser **negativo** (ej: -30 indica bus ya en la parada o proximo a salir).
- `sentido`: `null` cuando la parada tiene esa linea en ambos sentidos (~15% de los casos). Usar el campo `destino` para distinguir.

**Diferencia con `/cercanas`:** este endpoint devuelve hasta **10** tiempos por parada. `/cercanas` devuelve maximo **5**.

**Errores:**

| Codigo | Cuando |
|--------|--------|
| 500 | Error inesperado interno |
| 503 | API de TUSSAM no disponible (timeout, error HTTP) |

> **Nota:** a diferencia de `/cercanas` (que siempre devuelve 200), este endpoint **propaga errores** como 503.

---

### `GET /paradas/{codigo}/lineas`

Lineas que pasan por una parada.

```bash
curl http://localhost:8080/paradas/889/lineas
```

```json
["01", "24", "27", "C3"]
```

Devuelve un array de strings con los numeros de linea, ordenados alfabeticamente. Si la parada no existe o no tiene lineas asignadas, devuelve `[]`.

---

### `GET /lineas`

Todas las lineas de TUSSAM.

```bash
curl http://localhost:8080/lineas
```

```json
[
  {
    "numero": "01",
    "nombre": "Plg. Norte - H. Virgen del Rocio",
    "color": "#f54129",
    "updated_at": "2026-02-15 22:50:08"
  }
]
```

Ordenadas por `numero`. Actualmente devuelve 43 lineas.

---

### `GET /lineas/{numero}/paradas`

Paradas de una linea, ordenadas por sentido y posicion en el recorrido.

```bash
curl http://localhost:8080/lineas/01/paradas
```

```json
[
  {
    "sentido": 1,
    "orden": 0,
    "codigo": "252",
    "nombre": "Trabaj. Inmigrantes (Diego de Almagro)",
    "latitud": 37.412338,
    "longitud": -5.982419,
    "calle": "Avenida Diego de Almagro",
    "numero": "",
    "codigo_postal": "41008",
    "municipio": "Sevilla",
    "provincia": "Sevilla",
    "comunidad_autonoma": "Andalucia",
    "direccion_completa": "Avenida Diego de Almagro",
    "updated_at": "2026-02-16 20:41:18"
  },
  {
    "sentido": 2,
    "orden": 0,
    "codigo": "2",
    "nombre": "Menendez Pelayo (Puerta Carmona)",
    "latitud": 37.388370,
    "longitud": -5.984992
  }
]
```

- `sentido=1`: ida. `sentido=2`: vuelta.
- `orden`: posicion de la parada en el recorrido (empieza en 0).
- Si la linea no existe, devuelve `[]`.

---

### `GET /health`

Health check para Docker y load balancers.

```bash
curl http://localhost:8080/health
```

```json
{"status": "ok", "paradas_en_db": 967}
```

**Errores:**

| Codigo | Cuando |
|--------|--------|
| 503 | Base de datos no accesible |

---

### `GET /`

Informacion basica de la API.

```bash
curl http://localhost:8080/
```

```json
{"message": "TUSSAM API", "version": "1.1.0", "docs": null}
```

---

### Endpoints de Sincronizacion

Todos los endpoints `POST /sync/*` requieren `SYNC_API_KEY` configurada y header `X-API-Key`.

#### `POST /sync/all`

Sincronizacion completa: paradas + lineas + relaciones parada-linea.

```bash
curl -X POST http://localhost:8080/sync/all \
  -H "X-API-Key: tu-clave"
```

```json
{
  "message": "Sincronizacion completa",
  "paradas": 967,
  "lineas": 43,
  "paradas_lineas": 1756
}
```

#### `POST /sync/paradas`

Solo sincroniza paradas desde la API de TUSSAM.

```bash
curl -X POST http://localhost:8080/sync/paradas -H "X-API-Key: tu-clave"
```

```json
{"message": "Se sincronizaron 967 paradas"}
```

#### `POST /sync/lineas`

Solo sincroniza lineas.

```bash
curl -X POST http://localhost:8080/sync/lineas -H "X-API-Key: tu-clave"
```

```json
{"message": "Se sincronizaron 43 lineas"}
```

#### `POST /sync/paradas-lineas`

Sincroniza las relaciones parada-linea (que lineas pasan por cada parada y en que sentido).

```bash
curl -X POST http://localhost:8080/sync/paradas-lineas -H "X-API-Key: tu-clave"
```

```json
{"message": "Se sincronizaron 1756 relaciones parada-linea"}
```

#### `POST /sync/direcciones`

Geocodifica paradas sin direccion usando Nominatim (OpenStreetMap). Solo procesa paradas que aun no tienen `calle` asignada. Tarda ~17 minutos para 967 paradas nuevas (1 peticion/segundo a Nominatim).

```bash
curl -X POST http://localhost:8080/sync/direcciones -H "X-API-Key: tu-clave"
```

```json
{
  "message": "Geocodificacion completada",
  "total": 225,
  "ok": 224,
  "errors": 1
}
```

**Errores comunes a todos los endpoints de sync:**

| Codigo | Cuando |
|--------|--------|
| 403 | `X-API-Key` ausente o incorrecta |
| 503 | `SYNC_API_KEY` ausente/insegura o sync deshabilitado por configuracion |

---

## Filtros Avanzados

Todos estos filtros se aplican al endpoint `/cercanas`.

### Bearing (orientacion)

Filtra paradas segun la direccion a la que mira el usuario. Fundamental cuando hay paradas en aceras opuestas de una calle.

```
        N (0°)
        |
  O ----+---- E (90°)
 (270°) |
        S (180°)
```

- `bearing`: grados (0 = Norte, 90 = Este, 180 = Sur, 270 = Oeste)
- `bearing_tolerance`: diferencia maxima permitida (default 60°). Paradas fuera de esta tolerancia se descartan.

```bash
# Mirando al sur, solo paradas en esa direccion (±45°)
curl "http://localhost:8080/cercanas?lat=37.3891&lon=-5.9845&bearing=180&bearing_tolerance=45"
```

Con bearing, la respuesta incluye `bearing` (rumbo hacia la parada) y `bearing_diff` (diferencia angular). Los resultados se ordenan por `bearing_diff` (menor diferencia primero).

### Filtrar por lineas

```bash
# Solo buses de las lineas 01, C4 y 21
curl "http://localhost:8080/cercanas?lat=37.3891&lon=-5.9845&lineas=01,C4,21"
```

Las lineas se convierten automaticamente a mayusculas (`c4` → `C4`).

### Filtrar por sentido

```bash
# Solo sentido 1 (ida)
curl "http://localhost:8080/cercanas?lat=37.3891&lon=-5.9845&sentido=1"
```

- `sentido=1`: ida
- `sentido=2`: vuelta
- Sin filtro: ambos sentidos

Buses con `sentido=null` (linea presente en ambos sentidos en esa parada) pasan siempre el filtro.

### Filtrar por tiempo maximo

```bash
# Solo buses que llegan en los proximos 10 minutos
curl "http://localhost:8080/cercanas?lat=37.3891&lon=-5.9845&tiempo_max=10"
```

Este filtro excluye tiempos negativos (buses ya en la parada).

### Incluir mapa

```bash
curl "http://localhost:8080/cercanas?lat=37.3891&lon=-5.9845&incluir_mapa=true"
```

Anade un campo `mapa_url` a cada parada con un enlace a OpenStreetMap:

```
https://www.openstreetmap.org/?mlat=37.39125&mlon=-5.984236#map=18/37.39125/-5.984236
```

---

## Formatos de Respuesta

### JSON (por defecto)

Formato estandar con envelope `ubicacion` + `paradas`. Ver seccion de `/cercanas`.

### GeoJSON

```bash
curl "http://localhost:8080/cercanas?lat=37.3891&lon=-5.9845&formato=geojson"
```

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": {
        "type": "Point",
        "coordinates": [-5.984236, 37.391250]
      },
      "properties": {
        "codigo": "889",
        "nombre": "Recaredo (San Roque)",
        "distancia": 66,
        "tiempos": [
          {
            "linea": "01",
            "color": "#f54129",
            "tiempo_minutos": 4,
            "destino": "HOSPITAL V.ROCIO",
            "distancia_metros": 783,
            "sentido": 1
          }
        ]
      }
    }
  ]
}
```

**Diferencias con formato JSON:**

- `coordinates` sigue el estandar GeoJSON: **[longitud, latitud]** (lon primero).
- `properties` solo incluye: `codigo`, `nombre`, `distancia`, `tiempos`.
- No incluye: `bearing`, `bearing_diff`, `calle`, `direccion_completa`, `mapa_url`, `latitud`, `longitud`.
- El envelope `ubicacion` no existe en GeoJSON.

Compatible con librerias de mapas como Leaflet, Mapbox GL y OpenLayers.

---

## Codigos de Error

| Codigo | Significado | Cuando ocurre |
|--------|-------------|---------------|
| 400 | Parametro invalido | lat/lon fuera de rango, bearing fuera de [0,360], sentido != 1 o 2, formato no soportado |
| 403 | No autorizado | Endpoint de sync sin `X-API-Key` o con clave incorrecta |
| 404 | No encontrado | `GET /paradas/{codigo}` con codigo inexistente |
| 422 | Validacion fallida | Parametro fuera de rango (radio > 2000, max_paradas > 10, bearing_tolerance > 180, tiempo_max < 0) |
| 429 | Rate limit excedido | Mas de 60 req/min por dispositivo o 300 req/min por IP |
| 500 | Error interno | Error inesperado en `GET /paradas/{codigo}/tiempos` |
| 503 | Servicio no disponible | API de TUSSAM caida o base de datos no accesible |

Todas las respuestas de error siguen el formato:

```json
{"detail": "Descripcion del error"}
```

Las respuestas 422 (validacion de FastAPI) tienen formato extendido:

```json
{
  "detail": [
    {
      "type": "greater_than_equal",
      "loc": ["query", "radio"],
      "msg": "Input should be greater than or equal to 50",
      "input": "10"
    }
  ]
}
```

---

## Ejemplos Completos

### cURL

```bash
BASE="http://localhost:8080"

# Paradas cercanas con tiempos (endpoint principal)
curl "$BASE/cercanas?lat=37.3891&lon=-5.9845&max_paradas=2"

# Con bearing y filtro de linea
curl "$BASE/cercanas?lat=37.3891&lon=-5.9845&bearing=180&lineas=01,C4&sentido=1"

# Solo buses en los proximos 5 minutos
curl "$BASE/cercanas?lat=37.3891&lon=-5.9845&tiempo_max=5"

# Formato GeoJSON
curl "$BASE/cercanas?lat=37.3891&lon=-5.9845&formato=geojson"

# Con URL de mapa
curl "$BASE/cercanas?lat=37.3891&lon=-5.9845&incluir_mapa=true"

# Tiempos de una parada concreta
curl "$BASE/paradas/889/tiempos"

# Paradas de la linea 01
curl "$BASE/lineas/01/paradas"

# Sync completo (requiere API key)
curl -X POST "$BASE/sync/all" -H "X-API-Key: $SYNC_API_KEY"
```

### Python

```python
import httpx

BASE = "http://localhost:8080"
HEADERS = {"X-Device-ID": "mi-app-uuid-12345"}

# Paradas cercanas con tiempos
r = httpx.get(f"{BASE}/cercanas", params={
    "lat": 37.3891,
    "lon": -5.9845,
    "max_paradas": 2,
    "bearing": 180,
}, headers=HEADERS)

data = r.json()
for parada in data["paradas"]:
    print(f"{parada['nombre']} ({parada['distancia']}m)")
    for t in parada["tiempos"]:
        print(f"  Linea {t['linea']} -> {t['destino']} en {t['tiempo_minutos']} min")

# Tiempos de una parada concreta
r = httpx.get(f"{BASE}/paradas/889/tiempos", headers=HEADERS)
for t in r.json()["tiempos"]:
    print(f"Linea {t['linea']}: {t['tiempo_minutos']} min -> {t['destino']}")

# Todas las lineas
for linea in httpx.get(f"{BASE}/lineas", headers=HEADERS).json():
    print(f"Linea {linea['numero']}: {linea['nombre']} ({linea['color']})")

# Paradas de una linea
for p in httpx.get(f"{BASE}/lineas/01/paradas", headers=HEADERS).json():
    print(f"  [s{p['sentido']}] #{p['orden']} {p['nombre']}")

# Lineas que pasan por una parada
lineas = httpx.get(f"{BASE}/paradas/889/lineas", headers=HEADERS).json()
print(f"Parada 889: lineas {', '.join(lineas)}")

# Sync (requiere API key)
r = httpx.post(f"{BASE}/sync/all", headers={"X-API-Key": "tu-clave"})
print(r.json())
```

### Node.js

```javascript
const BASE = "http://localhost:8080";
const HEADERS = { "X-Device-ID": "mi-app-uuid-12345" };

// Paradas cercanas con tiempos
const params = new URLSearchParams({
  lat: 37.3891, lon: -5.9845, max_paradas: 2, bearing: 180,
});
const res = await fetch(`${BASE}/cercanas?${params}`, { headers: HEADERS });
const data = await res.json();

for (const parada of data.paradas) {
  console.log(`${parada.nombre} (${parada.distancia}m)`);
  for (const t of parada.tiempos) {
    console.log(`  Linea ${t.linea} -> ${t.destino} en ${t.tiempo_minutos} min`);
  }
}

// Tiempos de una parada
const tiempos = await fetch(`${BASE}/paradas/889/tiempos`, { headers: HEADERS });
const td = await tiempos.json();
for (const t of td.tiempos) {
  console.log(`Linea ${t.linea}: ${t.tiempo_minutos} min -> ${t.destino}`);
}

// Todas las lineas
const lineas = await (await fetch(`${BASE}/lineas`, { headers: HEADERS })).json();
for (const l of lineas) {
  console.log(`Linea ${l.numero}: ${l.nombre} (${l.color})`);
}

// Sync (requiere API key)
const sync = await fetch(`${BASE}/sync/all`, {
  method: "POST",
  headers: { "X-API-Key": "tu-clave" },
});
console.log(await sync.json());
```

### Swift (Apple Watch / iOS)

```swift
import Foundation

let base = "http://localhost:8080"
let deviceId = UIDevice.current.identifierForVendor?.uuidString ?? UUID().uuidString

// Paradas cercanas con tiempos
func fetchCercanas(lat: Double, lon: Double, bearing: Double?) async throws -> [String: Any] {
    var components = URLComponents(string: "\(base)/cercanas")!
    components.queryItems = [
        URLQueryItem(name: "lat", value: "\(lat)"),
        URLQueryItem(name: "lon", value: "\(lon)"),
        URLQueryItem(name: "max_paradas", value: "3"),
    ]
    if let bearing = bearing {
        components.queryItems?.append(URLQueryItem(name: "bearing", value: "\(bearing)"))
    }

    var request = URLRequest(url: components.url!)
    request.addValue(deviceId, forHTTPHeaderField: "X-Device-ID")

    let (data, _) = try await URLSession.shared.data(for: request)
    return try JSONSerialization.jsonObject(with: data) as! [String: Any]
}

// Uso
let result = try await fetchCercanas(lat: 37.3891, lon: -5.9845, bearing: 180)
if let paradas = result["paradas"] as? [[String: Any]] {
    for parada in paradas {
        print("\(parada["nombre"]!) (\(parada["distancia"]!)m)")
        if let tiempos = parada["tiempos"] as? [[String: Any]] {
            for t in tiempos {
                print("  Linea \(t["linea"]!) -> \(t["destino"]!) en \(t["tiempo_minutos"]!) min")
            }
        }
    }
}
```

---

## Sincronizacion Automatica

La API incluye un scheduler integrado (APScheduler) que sincroniza datos automaticamente.

**Configuracion por defecto:** cada domingo a las 04:00 UTC.

| Variable | Default | Descripcion |
|----------|---------|-------------|
| `SYNC_ENABLED` | `true` | Activar/desactivar el scheduler |
| `SYNC_DAY` | `sun` | Dia (mon, tue, wed, thu, fri, sat, sun) |
| `SYNC_HOUR` | `4` | Hora UTC (0-23) |
| `SYNC_MINUTE` | `0` | Minuto (0-59) |

El scheduler ejecuta automaticamente:

1. Sync de paradas desde la API de TUSSAM
2. Sync de lineas
3. Sync de relaciones parada-linea
4. Geocodificacion de paradas nuevas (Nominatim)

Si la fase 1 (paradas) falla, se aborta todo el sync. Las fases 2-4 son independientes: si una falla, las demas continuan.

Para desactivar: `SYNC_ENABLED=false`.

---

## Arquitectura

### Estructura del proyecto

```
TUSSAM/
├── app/
│   ├── main.py              # Endpoints FastAPI + rate limiting + auth
│   ├── database.py           # SQLite: tablas, queries, cache
│   ├── scheduler.py          # Sync semanal automatico (APScheduler)
│   └── services/
│       └── tussam.py         # Cliente API TUSSAM + geocodificacion Nominatim
├── data/
│   └── tussam.db             # SQLite con datos precargados (967 paradas, 43 lineas)
├── tests/
│   ├── conftest.py           # Fixtures compartidas
│   ├── test_database.py      # 23 tests de base de datos
│   ├── test_main.py          # 34 tests de endpoints
│   ├── test_tussam_service.py # 26 tests del servicio
│   ├── test_scheduler.py     # 8 tests del scheduler
│   └── test_e2e.py           # 30 tests end-to-end con API real
├── docs/
│   └── API.md                # Esta documentacion
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── README.md
```

### Stack tecnologico

| Componente | Tecnologia | Rol |
|------------|------------|-----|
| Framework web | FastAPI | Endpoints REST async |
| Servidor | uvicorn | ASGI server |
| HTTP client | httpx | Peticiones async a TUSSAM y Nominatim |
| Base de datos | SQLite + aiosqlite | Almacenamiento local async |
| Scheduler | APScheduler | Sync semanal automatico |
| Contenedor | Docker | Despliegue |

### Fuentes de datos

1. **API de TUSSAM** (`reddelineas.tussam.es`): paradas, lineas, tiempos de llegada en tiempo real. Rate limit estricto (429 si se supera). La API usa reintentos con backoff exponencial.

2. **Nominatim** (OpenStreetMap): geocodificacion inversa (coordenadas GPS → calle, numero, codigo postal). Limite: 1 peticion/segundo. Los resultados se cachean 30 dias.

### Variables de entorno

| Variable | Default | Descripcion |
|----------|---------|-------------|
| `SYNC_API_KEY` | *(vacio)* | API key para endpoints `/sync/*`. Sin valor = sync bloqueado (503) |
| `ALLOW_INSECURE_SYNC` | `false` | Solo desarrollo local: permite `/sync/*` sin API key cuando vale `true` |
| `ENABLE_API_DOCS` | `false` | Habilita `/docs`, `/redoc` y `/openapi.json` |
| `CORS_ALLOW_ORIGINS` | *(vacio)* | Lista CSV de origins permitidos. Si esta vacio, CORS queda desactivado |
| `SYNC_ENABLED` | `true` | Activar scheduler de sync semanal |
| `SYNC_DAY` | `sun` | Dia de sincronizacion |
| `SYNC_HOUR` | `4` | Hora UTC |
| `SYNC_MINUTE` | `0` | Minuto |

---

## Licencia

MIT - [686f6c61](https://github.com/686f6c61)
