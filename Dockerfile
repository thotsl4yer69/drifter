# MZ1312 DRIFTER — multi-stage container
# Stage 1: build the python wheel & deps. Stage 2: runtime image that also
# carries every src/*.py script flat under /opt/drifter so the v2.1
# services (fleet, mesh, recorder, replay, satellite, home, discord) can
# be launched directly via `python3 /opt/drifter/<module>.py`.
# UNCAGED TECHNOLOGY — EST 1991

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src
COPY README.md ./

RUN pip install --upgrade pip && \
    pip install --prefix=/install . && \
    pip install --prefix=/install \
        "paho-mqtt<2.0" \
        flask \
        flask-sock \
        zeroconf \
        "discord.py>=2.3" \
        pyyaml \
        requests

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DRIFTER_HOME=/var/lib/drifter \
    PATH="/usr/local/bin:/install/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
        libsndfile1 \
        ca-certificates \
        curl \
        iproute2 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -r -u 1000 -m -d ${DRIFTER_HOME} drifter

WORKDIR ${DRIFTER_HOME}

COPY --from=builder /install /usr/local
COPY --chown=drifter:drifter config ./config
COPY --chown=drifter:drifter assets ./assets

# Flat layout for the v2 services — same shape as /opt/drifter on the Pi.
RUN mkdir -p /opt/drifter
COPY --chown=drifter:drifter src/ /opt/drifter/
COPY --chown=drifter:drifter data/ /opt/drifter/data/
COPY --chown=drifter:drifter vehicles/ /opt/drifter/vehicles/

USER drifter

EXPOSE 8000 8420 8421/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["drifter-server", "--host", "0.0.0.0", "--port", "8000"]
