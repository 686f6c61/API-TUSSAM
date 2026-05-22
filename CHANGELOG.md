# Changelog

Todas las mejoras notables de este proyecto se documentan en este archivo.

El formato sigue [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) y el versionado [SemVer](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

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
