FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# python:*-slim ships no zoneinfo database, so without this the TZ variable is
# ignored by libc and every log line is stamped in UTC — confusing precisely
# when you are debugging the quiet hours.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY watcher/ ./watcher/

# The watcher only ever reads; running unprivileged costs nothing.
RUN useradd --create-home --uid 1000 watcher \
    && mkdir -p /data \
    && chown watcher:watcher /data
USER watcher

VOLUME /data

CMD ["python", "-m", "watcher.main"]
