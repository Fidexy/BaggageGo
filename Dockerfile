# BaggageGO — passenger demo (the gate), containerised.
#
# The build context is this directory and nothing else: the demo is
# self-contained, including the vendored brand kit at ./brand/. No sibling
# checkout, no ../brand, no extra build context.
#
#   docker build -t baggagego .
#   docker compose up            # see docker-compose.yml
#
# The database in here is a *seed*: `docker/entrypoint.sh` copies it onto the
# data volume on first start, and the running app never writes the image copy.

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Pins first: this layer is only rebuilt when requirements.txt changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The app. static/ carries the four bag glyphs; brand/ is the vendored kit that
# server_v1 serves over its three /bggo/brand/ routes.
COPY server_v1.py seed_passengers.py make_ticket_links.py ./
COPY static/ ./static/
COPY brand/ ./brand/

# The codes are read-only forever and the db is the seed for the volume.
COPY passenger_codes.txt ./
COPY passengers.db ./

# Tests come along so the image can prove itself:
#   docker compose run --rm demo pytest -q
COPY test_server_v1.py test_brand_vendored.py test_make_ticket_links.py ./

# The entrypoint is the only thing here that is not the app itself.
COPY docker/entrypoint.sh /app/docker/entrypoint.sh

# Where the app looks, and what it is allowed to write.
ENV BGGO_DB=/data/passengers.db \
    BGGO_PORT=8091 \
    BGGO_BRAND_DIR=/app/brand

# Non-root, and /data owned by that user: the volume mounts over it, so the
# ownership here is what a named volume inherits on first use.
RUN useradd --create-home --uid 10001 baggagego \
 && mkdir -p /data \
 && chown baggagego:baggagego /data \
 && chmod +x /app/docker/entrypoint.sh
USER baggagego

# pytest must not try to write its cache into a read-only /app (it warns otherwise)
ENV PYTEST_ADDOPTS="-p no:cacheprovider"

EXPOSE 8091

# No curl in slim: ask the app's own landing route. /bggo/ redirects, so probe
# the board itself. The port resolves exactly like the entrypoint's ($PORT wins).
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import os, urllib.request as u, sys; p = os.environ.get('PORT') or os.environ.get('BGGO_PORT') or '8091'; sys.exit(0 if u.urlopen(f'http://127.0.0.1:{p}/bggo/live', timeout=2).status == 200 else 1)"

ENTRYPOINT ["/app/docker/entrypoint.sh"]
