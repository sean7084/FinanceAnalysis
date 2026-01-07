# set base image
FROM python:3.12-slim-bullseye

# set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

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

# copy project
COPY . .
