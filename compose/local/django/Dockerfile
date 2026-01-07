# pull official base image
FROM python:3.11-slim-buster

# set work directory
WORKDIR /app

# set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# install dependencies
COPY ../../../requirements/local.txt .
RUN pip install -r local.txt

# copy project
COPY . .

# add start script
COPY ./start /start
RUN chmod +x /start
