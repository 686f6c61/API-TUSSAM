# Sitio estático de API TUSSAM servido con nginx.
# Coolify construye esta imagen desde la rama `landing` y la publica en
# tussam.686f6c61.dev (mismo patrón que el subdominio de Crupier).
FROM nginx:stable-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY . /usr/share/nginx/html/

# Se excluyen del sitio publicado los ficheros de build y las herramientas de
# desarrollo (no aportan nada al visitante y no deben servirse).
RUN rm -f \
      /usr/share/nginx/html/Dockerfile \
      /usr/share/nginx/html/.dockerignore \
      /usr/share/nginx/html/nginx.conf \
      /usr/share/nginx/html/social-card.html \
      /usr/share/nginx/html/validate.py \
      /usr/share/nginx/html/.gitignore \
    && chmod -R a=rX /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
