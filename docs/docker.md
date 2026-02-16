# Despliegue con Docker

Guia para levantar la API de TUSSAM con Docker. El repositorio incluye `Dockerfile` y `docker-compose.yml` listos para usar.

## Requisitos

- [Docker](https://docs.docker.com/get-docker/) >= 20.10
- [Docker Compose](https://docs.docker.com/compose/install/) >= 2.0 (incluido en Docker Desktop)

## Inicio rapido

```bash
# 1. Clonar el repositorio
git clone https://github.com/686f6c61/API-TUSSAM.git
cd API-TUSSAM

# 2. Configurar la API key para endpoints de administracion (opcional)
export SYNC_API_KEY=$(openssl rand -hex 32)
echo "SYNC_API_KEY=$SYNC_API_KEY"   # guardar esta clave

# 3. Arrancar
docker compose up -d

# 4. Verificar
curl http://localhost:8081/health
# {"status":"ok","paradas_en_db":967}
```

La API estara disponible en `http://localhost:8081`.

> La base de datos SQLite incluida (`data/tussam.db`) ya contiene 967 paradas, 43 lineas y 1,756 relaciones. No es necesario ejecutar ningun sync la primera vez.

## docker-compose.yml

```yaml
version: '3.8'

services:
  tussam:
    build: .
    container_name: tussam-api
    ports:
      - "8081:8080"          # puerto externo:interno
    volumes:
      - ./data:/app/data     # persistir la DB fuera del contenedor
    environment:
      - SYNC_ENABLED=true
      - SYNC_DAY=sun
      - SYNC_HOUR=4
      - SYNC_MINUTE=0
      - SYNC_API_KEY=${SYNC_API_KEY:-cambia-esta-clave}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 5s
      retries: 3
    restart: unless-stopped
```

### Explicacion

| Campo | Descripcion |
|-------|-------------|
| `ports: "8081:8080"` | El contenedor escucha en 8080 internamente, se mapea al 8081 del host. Cambiar el primer numero para usar otro puerto. |
| `volumes: ./data:/app/data` | Monta la carpeta `data/` local dentro del contenedor. La base de datos SQLite persiste entre reinicios y actualizaciones. |
| `restart: unless-stopped` | Se reinicia automaticamente si el proceso falla o si Docker se reinicia. Solo se detiene si se para manualmente. |
| `healthcheck` | Docker verifica cada 30s que `/health` responde. Si falla 3 veces consecutivas, el contenedor se marca como unhealthy. |

## Dockerfile

```dockerfile
FROM python:3.9-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY . .

RUN mkdir -p /app/data

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

La imagen usa `python:3.9-slim` (~150 MB). Se instalan las dependencias antes de copiar el codigo fuente para aprovechar la cache de capas de Docker.

## Variables de entorno

| Variable | Default | Descripcion |
|----------|---------|-------------|
| `SYNC_API_KEY` | *(vacio)* | Clave para proteger los endpoints `/sync/*`. Si esta vacia, los endpoints de sync quedan abiertos. |
| `SYNC_ENABLED` | `true` | Activar la sincronizacion automatica semanal con la API de TUSSAM. |
| `SYNC_DAY` | `sun` | Dia de la semana para el sync (`mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun`). |
| `SYNC_HOUR` | `4` | Hora UTC del sync (0-23). |
| `SYNC_MINUTE` | `0` | Minuto del sync (0-59). |

Para definir la API key en produccion:

```bash
# Opcion 1: Variable de entorno del shell
export SYNC_API_KEY=mi-clave-secreta
docker compose up -d

# Opcion 2: Archivo .env (no subir al repo)
echo "SYNC_API_KEY=mi-clave-secreta" > .env
docker compose up -d
```

> El archivo `.env` ya esta incluido en `.gitignore`.

## Persistencia de datos

El volumen `./data:/app/data` garantiza que la base de datos SQLite sobreviva a:

- `docker compose down` + `docker compose up`
- `docker compose up --build` (rebuild de la imagen)
- Actualizaciones del codigo

Para hacer backup:

```bash
# Copiar la DB (mientras el contenedor esta corriendo es seguro, SQLite usa WAL)
cp data/tussam.db data/tussam.db.backup
```

Para restaurar:

```bash
docker compose down
cp data/tussam.db.backup data/tussam.db
docker compose up -d
```

## Comandos utiles

```bash
# Ver logs en tiempo real
docker compose logs -f tussam

# Ver el estado del contenedor
docker compose ps

# Reiniciar el contenedor
docker compose restart tussam

# Parar
docker compose down

# Rebuild (despues de actualizar codigo)
docker compose up -d --build

# Shell dentro del contenedor
docker compose exec tussam bash
```

## Verificar que funciona

```bash
# Health check
curl http://localhost:8081/health
# {"status":"ok","paradas_en_db":967}

# Paradas cercanas a Puerta de Carmona
curl "http://localhost:8081/cercanas?lat=37.3886&lon=-5.9850&max_paradas=2"

# Forzar sincronizacion manual (requiere SYNC_API_KEY)
curl -X POST http://localhost:8081/sync/all \
  -H "X-API-Key: $SYNC_API_KEY"
```

## Cambiar el puerto

Editar `docker-compose.yml`:

```yaml
ports:
  - "3000:8080"    # ahora la API escucha en http://localhost:3000
```

```bash
docker compose down && docker compose up -d
```

## Actualizar a una nueva version

```bash
# Descargar cambios
git pull

# Rebuild y reiniciar (la DB persiste por el volumen)
docker compose up -d --build
```

## Troubleshooting

### El puerto ya esta en uso

```
Error: Bind for 0.0.0.0:8081 failed: port is already allocated
```

Cambiar el puerto en `docker-compose.yml` o liberar el 8081:

```bash
# Encontrar que proceso usa el puerto
lsof -i :8081
```

### El contenedor no arranca

```bash
# Ver los logs para diagnosticar
docker compose logs tussam

# Verificar que la DB existe
ls -la data/tussam.db
```

### Health check falla

Si `docker compose ps` muestra `(unhealthy)`:

```bash
# Verificar manualmente
docker compose exec tussam curl http://localhost:8080/health

# Ver logs
docker compose logs --tail 50 tussam
```

### Permisos en la base de datos

Si la DB no se puede escribir (errores de `OperationalError: database is locked`):

```bash
# Verificar permisos del volumen
ls -la data/
chmod 666 data/tussam.db
docker compose restart tussam
```

---

Para la documentacion completa de todos los endpoints, parametros y ejemplos, ver [API.md](API.md).
