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
