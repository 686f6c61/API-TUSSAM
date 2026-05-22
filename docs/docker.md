# Despliegue con Docker

Guía para levantar la API de TUSSAM con Docker. El repositorio incluye `Dockerfile` y `docker-compose.yml` listos para usar.

## Requisitos

- [Docker](https://docs.docker.com/get-docker/) >= 20.10
- [Docker Compose](https://docs.docker.com/compose/install/) >= 2.0

## Inicio rápido

```bash
git clone https://github.com/686f6c61/API-TUSSAM.git
cd API-TUSSAM

# Configurar API key para endpoints de sync
export SYNC_API_KEY=$(openssl rand -hex 32)

# Arrancar
docker compose up -d

# Verificar
curl http://localhost:8081/health
# {"status":"ok","db":"connected","paradas_en_db":967,"version":"1.0.2"}
```

La API estará disponible en `http://localhost:8081`. La base de datos incluida (`data/tussam.db`) ya contiene 967 paradas, 49 líneas y 1.756 relaciones.

## docker-compose.yml

```yaml
services:
  tussam:
    build: .
    container_name: tussam-api
    ports:
      - "8081:8080"
    volumes:
      - ./data:/app/data
    environment:
      - APP_ENV=${APP_ENV:-development}
      - ENABLE_DOCS=${ENABLE_DOCS:-true}
      - CORS_ORIGINS=${CORS_ORIGINS:-*}
      - ALLOWED_HOSTS=${ALLOWED_HOSTS:-}
      - SYNC_ENABLED=true
      - SYNC_DAY=sun
      - SYNC_HOUR=4
      - SYNC_MINUTE=0
      - SYNC_API_KEY=${SYNC_API_KEY:?Define SYNC_API_KEY antes de arrancar docker compose}
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]
      interval: 30s
      timeout: 5s
      retries: 3
    restart: unless-stopped
```

| Campo | Descripción |
|-------|-------------|
| `ports: "8081:8080"` | El contenedor escucha en 8080, mapeado al 8081 del host |
| `volumes: ./data:/app/data` | La DB SQLite persiste entre reinicios |
| `restart: unless-stopped` | Reinicio automático si falla el proceso |
| `healthcheck` | Verifica `/health` cada 30s |

## Dockerfile

```dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system tussam \
    && adduser --system --ingroup tussam --home /app tussam

COPY pyproject.toml README.md LICENSE ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY data ./data

RUN mkdir -p /app/data \
    && chown -R tussam:tussam /app

USER tussam

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

## Variables de entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `APP_ENV` | `development` | Entorno de ejecución (`production` aplica defaults más restrictivos) |
| `SYNC_API_KEY` | *(requerida)* | Clave para proteger `/sync/*` |
| `ALLOW_UNAUTHENTICATED_SYNC` | `false` | Permite sync sin API key solo para desarrollo local explícito |
| `ENABLE_DOCS` | `true` en dev, `false` en prod | Habilitar `/docs`, `/redoc` y `/openapi.json` |
| `CORS_ORIGINS` | `*` en dev, vacío en prod | Orígenes CORS separados por coma |
| `ALLOWED_HOSTS` | vacío | Hosts permitidos separados por coma |
| `TIEMPOS_CACHE_TTL_SECONDS` | `60` | TTL de cache fresca para tiempos de llegada |
| `TIEMPOS_STALE_TTL_SECONDS` | `600` | Tiempo máximo para devolver cache antigua si TUSSAM falla |
| `TUSSAM_MAX_CONCURRENT_REQUESTS` | `4` | Límite de concurrencia saliente hacia TUSSAM |
| `TUSSAM_SYNC_REQUEST_DELAY_SECONDS` | `0.2` | Pausa entre peticiones de sincronización a TUSSAM |
| `SYNC_ENABLED` | `true` | Activar sincronización semanal automática |
| `SYNC_DAY` | `sun` | Día de la semana (`mon`-`sun`) |
| `SYNC_HOUR` | `4` | Hora UTC (0-23) |
| `SYNC_MINUTE` | `0` | Minuto (0-59) |

```bash
# Opción 1: variable de entorno
export SYNC_API_KEY=$(openssl rand -hex 32)
docker compose up -d

# Opción 2: archivo .env (no subir al repo)
echo "SYNC_API_KEY=$(openssl rand -hex 32)" > .env
docker compose up -d
```

## Persistencia de datos

El volumen `./data:/app/data` garantiza que la DB sobreviva a rebuilds y reinicios.

```bash
# Backup
cp data/tussam.db data/tussam.db.backup

# Restaurar
docker compose down
cp data/tussam.db.backup data/tussam.db
docker compose up -d
```

## Comandos útiles

```bash
docker compose logs -f tussam     # logs en tiempo real
docker compose ps                  # estado del contenedor
docker compose restart tussam      # reiniciar
docker compose down                # parar
docker compose up -d --build       # rebuild tras cambios de código
docker compose exec tussam bash    # shell dentro del contenedor
```

## Verificar que funciona

```bash
# Health check
curl http://localhost:8081/health

# Paradas cercanas a Puerta de Carmona
curl "http://localhost:8081/cercanas?lat=37.3886&lon=-5.9850&max_paradas=2"

# Sincronización manual (requiere API key)
curl -X POST http://localhost:8081/sync/all -H "X-API-Key: $SYNC_API_KEY"
```

## Cambiar el puerto

```yaml
ports:
  - "3000:8080"
```

```bash
docker compose down && docker compose up -d
```

## Actualizar a una nueva versión

```bash
git pull
docker compose up -d --build
```

## Troubleshooting

### Puerto en uso

```bash
lsof -i :8081
```

Cambiar el puerto en `docker-compose.yml`.

### Health check falla (`unhealthy`)

```bash
docker compose exec tussam python3 -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8080/health').read())"
docker compose logs --tail 50 tussam
```

### DB bloqueada (`database is locked`)

```bash
chmod 666 data/tussam.db
docker compose restart tussam
```

---

Para la documentación completa de endpoints, parámetros y ejemplos, ver [API.md](API.md).
