#!/bin/sh

set -o errexit
set -o nounset

# We are using the default postgres settings, but it's good practice to wait for the database to be ready
# See: https://docs.docker.com/compose/startup-order/
# The official postgres image will create the database and user on startup.

python manage.py migrate
python manage.py collectstatic --noinput

exec "$@"
