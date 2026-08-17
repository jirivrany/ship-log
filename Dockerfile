FROM python:3.12-slim

WORKDIR /app

# WeasyPrint (PDF export) needs the Pango text stack
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Don't run as root. No fixed uid: the host's uid is supplied at run time via
# `user:` in docker-compose.prod.yml, because uid 1000 is the developer's
# account on a laptop but the sudo admin account on the server.
RUN useradd -m app && chown -R app:app /app
USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
