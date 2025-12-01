# ===================================================================
# DOCKERFILE PARA SISTEMA DE AUDITORIA DE URGENCIAS
# ===================================================================
# Multi-stage build optimizado para produccion con uv (Astral)
# Compatible con Dokploy Schedules
# ===================================================================

# -------------------------------------------------------------------
# STAGE 1: Builder - Instala dependencias con uv
# -------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

# Optimizaciones de uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Instalar dependencias usando cache de Docker
# Esto permite reconstruir mas rapido si solo cambio el codigo
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --no-install-project --no-dev

# Copiar codigo fuente
COPY . /app

# Sincronizar proyecto completo
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev

# -------------------------------------------------------------------
# STAGE 2: Runtime - Imagen final sin uv (mas ligera)
# -------------------------------------------------------------------
FROM python:3.13-slim-bookworm

# Metadata
LABEL maintainer="CAF Auditoria"
LABEL description="Sistema de Auditoria de Urgencias - Clinica Foianini"
LABEL version="1.0.0"

# Crear usuario no-root para mayor seguridad
RUN groupadd --system --gid 999 auditor && \
    useradd --system --gid 999 --uid 999 --create-home auditor

# Copiar aplicacion desde builder
COPY --from=builder --chown=auditor:auditor /app /app

# Configurar PATH para usar el virtualenv
ENV PATH="/app/.venv/bin:$PATH"

# Crear directorios para outputs (ya no se usan localmente, van a MinIO)
# Pero los mantenemos por si acaso se necesitan temporalmente
RUN mkdir -p /app/output /app/logs && \
    chown -R auditor:auditor /app/output /app/logs

# Cambiar a usuario no-root
USER auditor

# Establecer directorio de trabajo
WORKDIR /app

# -------------------------------------------------------------------
# COMANDO DE INICIO
# -------------------------------------------------------------------
# IMPORTANTE: El contenedor debe mantenerse activo para que Dokploy
# pueda ejecutar comandos mediante schedules.
#
# Usamos 'tail -f /dev/null' como proceso principal que:
# - Mantiene el contenedor corriendo indefinidamente
# - No consume recursos significativos
# - Permite a Dokploy ejecutar 'docker exec' para correr los scripts
# -------------------------------------------------------------------
CMD ["tail", "-f", "/dev/null"]

# -------------------------------------------------------------------
# NOTAS DE USO CON DOKPLOY
# -------------------------------------------------------------------
# 1. El contenedor estara siempre corriendo (tail -f /dev/null)
# 2. Dokploy ejecutara scripts mediante schedules:
#    - Auditoria diaria: python main.py
#    - Auditoria individual: python auditar_atencion.py 2025/130411
# 3. Los archivos se guardan en MinIO (bucket: auditoria-urgencias)
# 4. Logs disponibles via docker logs o en MinIO
# -------------------------------------------------------------------
