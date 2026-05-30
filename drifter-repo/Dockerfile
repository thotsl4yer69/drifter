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
    pip install --prefix=/install .

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DRIFTER_HOME=/var/lib/drifter \
    PATH="/usr/local/bin:/install/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -r -u 1000 -m -d ${DRIFTER_HOME} drifter

WORKDIR ${DRIFTER_HOME}

COPY --from=builder /install /usr/local
COPY --chown=drifter:drifter config ./config
COPY --chown=drifter:drifter assets ./assets

USER drifter

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["drifter-server", "--host", "0.0.0.0", "--port", "8000"]
