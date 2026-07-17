# Changelog

Todas las mejoras notables de este proyecto se documentan en este archivo.

El formato sigue [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) y el versionado [SemVer](https://semver.org/spec/v2.0.0.html).

---

## [2.0.0] - 2026-07-17

Versión mayor: endurecimiento para producción tras una auditoría de seguridad, escalabilidad, arquitectura y fiabilidad, más una web de presentación, documentación y mapa de paradas. Se sube a `2.0.0` (y no a `1.1.0`) porque incluye cambios incompatibles en el contrato de la API y en la licencia.

### Roto (cambios incompatibles)

- **`/cercanas`: el campo `direccion` se renombra a `direccion_completa`** para unificarlo con `GET /paradas/{codigo}`. Los clientes que leían `direccion` deben actualizarse.
- **`/cercanas`: `municipio` y `codigo_postal` ya no traen valores por defecto fabricados** (antes `"Sevilla"`/`""`); ahora reflejan el dato real y pueden ser `null`.
- **Validación de formato en el borde**: un `codigo` de parada no numérico o un número de línea no alfanumérico devuelven `422` (antes podían acabar en `404` o `200`).
- **Licencia**: cambia de MIT a PolyForm Noncommercial 1.0.0. El uso comercial deja de estar permitido sin acuerdo con el autor (ver sección «Cambiado»).

### Seguridad

- **Rate limiting combinado IP + dispositivo**: los dos cubos se aplican de forma conjunta. El `X-Device-ID` (que elige el cliente) ya no puede sustituir al límite por IP, solo restringir más. Cierra el bypass por rotación de identificadores.
- **IP real tras proxy**: uvicorn arranca con `--proxy-headers` y se añade `TRUSTED_PROXY_IPS` para derivar la IP del cliente desde `X-Forwarded-For` de proxys de confianza.
- **Validación de formato en el borde**: los códigos de parada deben ser numéricos y los de línea alfanuméricos (`422` si no), evitando inyección en logs y metacaracteres en la petición saliente.
- **Cabeceras de seguridad HTTP**: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` y `Strict-Transport-Security` (en producción).
- **Sin redirecciones salientes**: `follow_redirects=False` en el cliente hacia el origen para prevenir SSRF.
- **`ALLOW_UNAUTHENTICATED_SYNC` bloqueado en producción** y `/health` exento del rate limiting.

### Fiabilidad

- **Integridad de escritura**: todas las escrituras a SQLite se serializan bajo un lock, de modo que cada transacción (incluido el `DELETE`+`INSERT` de relaciones) es atómica frente a las consultas concurrentes.
- **Sync sin pérdida de datos**: un sync que no recupera datos del origen (Cloudflare, cambio de contrato) aborta sin machacar el catálogo; las relaciones solo se reemplazan si el sync fue completo.
- **Guardián de completitud del sync**: la API de TUSSAM solo lista las líneas activas en el instante de la consulta, así que sincronizar en una franja de baja actividad (madrugada) devolvía una fracción de la red y el `DELETE` + reinserción de relaciones la habría mutilado. Ahora el sync aborta (`502`) si los datos recibidos no alcanzan una proporción mínima del catálogo actual (`SYNC_MIN_COMPLETENESS_RATIO`, por defecto 0,8). Además, el sync semanal pasa a mediodía UTC (`SYNC_HOUR=11`) para capturar la red completa.
- **Migración de esquema no silenciosa**: los errores de `ALTER TABLE` distintos de «columna duplicada» se propagan en el arranque en lugar de dejar un esquema incompleto.
- **Señalización de estado del origen en `/cercanas`**: cada parada incluye `tiempos_status` (`ok`/`unavailable`/`error`) y propaga `stale`/`cached_at`, distinguiendo «no vienen buses» de «no pudimos consultar».
- **Estimaciones sin `segundos` descartadas** (ya no aparecen como «bus llega ya») y coordenadas ausentes como `null` en lugar de `(0, 0)`.
- **Health check con causa registrada** y estado `no_data` cuando la base de datos está vacía; resumen honesto del scheduler cuando alguna fase falla.

### Escalabilidad y arquitectura

- **`/cercanas` paraleliza** la obtención de tiempos de las paradas con `asyncio.gather`.
- **Limpieza periódica de la cache** de tiempos para acotar el crecimiento del fichero SQLite.
- **Sincronización serializada de extremo a extremo**: un único lock compartido entre los endpoints `/sync/*` y el job del scheduler evita que un sync manual y el semanal se solapen (`409` en el endpoint, el job se omite si hay uno en curso).
- **`/sync/all` reporta resultados parciales**: si una fase falla, las anteriores conservan su recuento; un `RuntimeError` del origen se traduce a `502` con el motivo en lugar de un `500` opaco.
- **Configuración centralizada** en `app/env.py`: los helpers de lectura de variables de entorno dejan de estar duplicados en tres módulos.
- **TTL de cache en UTC** de forma consistente con el scheduler; despliegue con un solo worker documentado, con guía para escalar por réplicas.

### Cambiado

- **Licencia**: el proyecto pasa de MIT a **PolyForm Noncommercial License 1.0.0**. Se permite usar, modificar y distribuir el software solo con fines no comerciales (uso personal, investigación, educación y organizaciones sin ánimo de lucro); el uso comercial requiere acuerdo con el autor.
- **Despliegue Docker**: `uvicorn` arranca con `--proxy-headers` y un solo worker (los cubos de rate limiting y los locks viven en memoria del proceso; se escala por réplicas, no por workers).
- **Programación del sync semanal**: pasa de las 04:00 a las **11:00 UTC** para capturar la red con todas las líneas en servicio.
- **Nuevas variables de entorno**: `TRUSTED_PROXY_IPS`, `FORWARDED_ALLOW_IPS` (IP real tras proxy) y `SYNC_MIN_COMPLETENESS_RATIO` (guardián de completitud del sync).

### Añadido

- **Módulo `app/env.py`** con la lectura centralizada y validada de variables de entorno (antes duplicada en tres módulos).
- **Helpers de base de datos** para el nuevo comportamiento: contadores (`count_lineas`, `count_paradas_lineas`), comprobación de existencia (`parada_exists`, `linea_exists`) y purga de cache (`purge_tiempos_cache`).
- **Sitio web** (landing, documentación navegable y **mapa interactivo de paradas** con búsqueda y filtros por línea y por zona), publicado como sitio estático desde la rama `landing` en `https://tussam.686f6c61.dev/`. Incluye SEO completo (canonical, Open Graph, `sitemap.xml`, `robots.txt`) e imagen social 1200×630 para compartir la URL. No forma parte de la rama `main`.

### Eliminado

- **`examples/smoke-app`** y el perfil `smoke` de Docker Compose: la validación en navegador se sustituye por el sitio web y su mapa de paradas.

### Tests

- Suite ampliada a **104 tests unitarios** que cubren el rate limiting combinado IP + dispositivo, el guardián de completitud del sync, el sync parcial (`502`), la validación de formato en el borde (`422`), el cache con TTL en UTC y los locks perezosos ligados al event loop.

## [1.0.4] - 2026-05-22

### Mejorado

- `GET /paradas/{codigo}/tiempos` ya no propaga un `503` cuando TUSSAM no está disponible: devuelve `200` con `tiempos: []` y metadata `upstream_status` para mantener estable el contrato de la app.
- Actualizada la documentación del endpoint de tiempos para distinguir caídas de upstream de errores internos.

## [1.0.3] - 2026-05-22

### Añadido

- App estática de smoke test en `examples/smoke-app` para validar Docker desde navegador con geocodificación de dirección a coordenadas.
- Perfil opcional `smoke` en Docker Compose para publicar la app en `http://localhost:8082`.

### Eliminado

- Carpeta `landing/`, que no forma parte de la API ni del despliegue Docker.
- Scripts standalone históricos de mantenimiento; la geocodificación se mantiene dentro de `/sync/direcciones` y del scheduler.

## [1.0.2] - 2026-05-22

### Añadido

- Single-flight por parada para deduplicar peticiones simultáneas de tiempos.
- Fallback con cache antigua cuando TUSSAM no responde y hay datos recientes disponibles.
- Variables de entorno para ajustar TTL de cache, cache antigua, concurrencia saliente y pausa de sincronización.

### Mejorado

- El cliente TUSSAM respeta `Retry-After` en respuestas 429/5xx y limita la concurrencia saliente.
- Los syncs de paradas y líneas usan la misma capa de reintentos que los tiempos en tiempo real.

## [1.0.1] - 2026-05-22

### Seguridad

- Los endpoints `POST /sync/*` ahora requieren `SYNC_API_KEY` por defecto y fallan cerrado si no está configurada.
- Eliminado el valor por defecto de `SYNC_API_KEY` en Docker Compose.
- Añadida configuración por entorno para docs, CORS y hosts permitidos.
- El contenedor Docker se ejecuta con usuario no-root.

### Corregido

- El parser de tiempos tolera respuestas vacías o con forma inesperada de la API de TUSSAM sin devolver 500.
- `/health` cuenta paradas con una query agregada en vez de cargar toda la tabla.
- Las respuestas públicas usan modelos explícitos para mantener estable el contrato de la API.
- Las fechas de SQLite se guardan como strings ISO explícitos para evitar warnings de adaptadores `datetime`.

### DevOps

- Añadido `.dockerignore` para reducir el contexto de build.
- El workflow de GitHub ejecuta compile, lint, tests unitarios y auditoría de dependencias antes de construir y publicar la imagen Docker.
- Actualizada documentación pública de despliegue y variables de entorno.

## [1.0.0] - 2026-05-22

### Añadido

- Endpoint principal `GET /cercanas`: paradas cercanas con tiempos de llegada en una sola llamada
- `GET /paradas/cercanas`: paradas cercanas sin tiempos (solo coordenadas)
- `GET /paradas/{codigo}`: datos de una parada específica
- `GET /paradas/{codigo}/tiempos`: tiempos de llegada con `vehiculo`, `atributos` y `sentido`
- `GET /paradas/{codigo}/lineas`: líneas que pasan por una parada
- `GET /lineas`: todas las líneas con `sublinea`, `hora_inicio`, `hora_fin`
- `GET /lineas/{numero}/paradas`: paradas de una línea con sentido y orden
- `GET /health`: health check con verificación de base de datos
- `POST /sync/paradas`: sincronizar paradas desde API de TUSSAM
- `POST /sync/lineas`: sincronizar líneas con horarios de operación
- `POST /sync/paradas-lineas`: sincronizar relaciones parada-línea
- `POST /sync/all`: sincronización completa (paradas + líneas + relaciones)
- 967 paradas precargadas con calle, número, CP, municipio y provincia
- 49 líneas con horarios de operación (horaInicio / horaFin)
- 1.756 relaciones parada-línea con sentido y orden
- Geocodificación inversa de todas las paradas con Nominatim (OpenStreetMap)
- Cache de tiempos de llegada con TTL de 1 minuto (SQLite)
- Conexión única persistente a SQLite con modo WAL y busy_timeout
- Prefiltrado por bounding box (reduce ~85% de cálculos Haversine)
- Rate limiting por dispositivo (`X-Device-ID`, 60 req/min) y por IP (300 req/min)
- Autenticación por API Key (`X-API-Key`) en endpoints de sync
- Scheduler semanal de sincronización (APScheduler, configurable por variables de entorno)
- Contenedor Docker con health check (Python 3.11-slim)
- Sistema de reintentos con backoff exponencial para rate limits de TUSSAM
- Filtrado por orientación (`bearing`) del usuario con tolerancia configurable
- Filtrado por líneas específicas, tiempo máximo y sentido
- Respuestas en formato GeoJSON (`formato=geojson`)
- Script standalone de geocodificación (`scripts/geocode_paradas.py`)
- Script de escaneo de cambios (`scripts/scan_changes.py`)
- Landing page completa con aviso legal, cookies y privacidad
- 78 tests unitarios (base de datos, endpoints, servicio)
- Documentación completa en `docs/API.md` con diagramas Mermaid
- `README.md` con arquitectura, flujo de datos, esquema DB y ejemplos
- `LICENSE` (MIT) y `CHANGELOG.md`

### Capturado de la API de TUSSAM

- `vehiculo`: ID único del autobús
- `atributos`: array reservado para accesibilidad, wifi, tipo de bus (hoy vacío)
- `sublinea`: variante de línea
- `horaInicio` / `horaFin`: horario de operación por sentido
- `sentido`: 1 (ida) o 2 (vuelta) resuelto por relaciones parada-línea
- `destino.esReal`: indica si el tiempo es GPS real vs estimado por horario

### Mejoras técnicas

- Conexión SQLite persistente (antes: abrir/cerrar en cada query)
- Bounding box en `get_paradas_cercanas` (antes: Haversine en 967 paradas)
- CSS de landing extraído a `common.css` (antes: duplicado en 3 páginas)
- `python-dotenv` eliminado de dependencias (no se usaba)
- Python 3.9 → 3.11 en Dockerfile
- Eliminada tabla `direcciones_cache` (las direcciones están en `paradas`)
- Eliminado código muerto: `PHOTON_REVERSE_URL`, `get_direccion_from_coords`
- Eliminado directorio `worker/` (no se usa Cloudflare)
- Health check con verificación real de DB (antes: solo count)
