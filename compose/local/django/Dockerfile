# ===========================================================================
# Frontend build stage
# ---------------------------------------------------------------------------
# Produces /frontend/dist containing the hashed Vite bundle and
# /frontend/dist/.vite/manifest.json consumed by django-vite at runtime.
# Kept as a separate stage so the Python runtime image never carries Node,
# npm, or node_modules.
#
# Vite 8 requires Node >=20.19 or >=22.12, so we pin to node:22-alpine.
# ===========================================================================
FROM node:22-alpine AS frontend-build

WORKDIR /frontend

# Install deps first for layer caching; only package*.json changes bust this
# layer, so source-only edits reuse the cached npm ci.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


# ===========================================================================
# Django runtime stage
# ===========================================================================
# set base image
FROM python:3.12-slim-bullseye

# set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Install TA-Lib C library
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    libgomp1 \
    wget && \
    wget -q http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz -O ta-lib-0.4.0-src.tar.gz && \
    tar -xzf ta-lib-0.4.0-src.tar.gz && \
    cd ta-lib/ && \
    ./configure --prefix=/usr && \
    make && \
    make install && \
    cd .. && \
    rm -rf ta-lib* && \
    apt-get purge -y --auto-remove build-essential wget

# set work directory
WORKDIR /app

# install dependencies
COPY ./requirements/ /app/requirements/
RUN pip install -r /app/requirements/local.txt


# copy entrypoint script
COPY ./compose/local/django/entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r//' /entrypoint.sh
RUN chmod +x /entrypoint.sh

# copy start script
COPY ./compose/local/django/start.sh /start
RUN sed -i 's/\r//' /start
RUN chmod +x /start

COPY ./compose/local/django/start-celeryworker /start-celeryworker
RUN sed -i 's/\r//' /start-celeryworker
RUN chmod +x /start-celeryworker

COPY ./compose/local/django/start-celerybeat /start-celerybeat
RUN sed -i 's/\r//' /start-celerybeat
RUN chmod +x /start-celerybeat


# copy project
COPY . .

# Overlay the freshly built frontend bundle. Placed AFTER `COPY . .` because
# .dockerignore excludes frontend/dist from the build context, but if that
# exclusion is ever removed we still want the image-baked dist to win.
COPY --from=frontend-build /frontend/dist /app/frontend/dist
