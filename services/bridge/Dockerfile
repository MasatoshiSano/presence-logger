FROM python:3.11-slim-bookworm

# nmcli (NetworkManager client) and Instant Client deps.
RUN apt-get update && apt-get install -y --no-install-recommends \
        network-manager libaio1 ca-certificates wget unzip \
    && rm -rf /var/lib/apt/lists/*

# Optional: install Oracle Instant Client Basic Light (Linux ARM64).
# Caller passes INSTANT_CLIENT_URL at build time. If unset, Thin mode only.
ARG INSTANT_CLIENT_URL=""
RUN if [ -n "$INSTANT_CLIENT_URL" ]; then \
        mkdir -p /opt/oracle && \
        wget -q -O /tmp/ic.zip "$INSTANT_CLIENT_URL" && \
        unzip -q /tmp/ic.zip -d /opt/oracle && \
        mv /opt/oracle/instantclient_* /opt/oracle/instantclient && \
        rm /tmp/ic.zip ; \
    fi

ENV LD_LIBRARY_PATH=/opt/oracle/instantclient

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./services/bridge/src/

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["python", "-m", "services.bridge.src.main"]
