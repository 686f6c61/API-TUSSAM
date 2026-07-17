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

# --proxy-headers hace que uvicorn interprete X-Forwarded-For/Proto tras un
# proxy, de modo que request.client.host sea la IP real del cliente (necesario
# para que el rate limiting por IP funcione). La confianza en el proxy se acota
# con la variable de entorno FORWARDED_ALLOW_IPS (por defecto, la red local).
# Un solo worker: el rate limiter y los locks de single-flight viven en memoria
# del proceso; escalar por réplicas, no por workers, para no multiplicar límites.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--workers", "1"]
