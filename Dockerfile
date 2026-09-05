# =============================================================================
# Multi-stage build.
#
# Stage 1 installs the dependencies into a virtual environment; stage 2 copies
# only that environment and the application, so compilers and build caches
# never reach the published image.
#
# The catalogue is copied into the image as data. That is deliberate: the
# container is useless without it, and a compliance tool that loads its control
# set from somewhere writable at run time is a compliance tool whose Statement
# of Applicability can be changed without a deployment.
#
# The container runs as a non-root user, asserted in CI rather than trusted.
# =============================================================================

# ---------- Stage 1: builder -------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --upgrade pip setuptools wheel && pip install .

# ---------- Stage 2: runtime -------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="iso27001-cybersecurity-copilot" \
      org.opencontainers.image.description="Deterministic ISO/IEC 27001:2022 compliance engine: SoA, risk register, evidence freshness and an audit-readiness gate." \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/legend-cell05/iso27001-cybersecurity-copilot"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SENTINEL_LOG_FORMAT=json \
    SENTINEL_DATA_DIR=/app/data \
    SENTINEL_CATALOGUE_DIR=/app/catalogue

RUN apt-get update \
    && apt-get install --no-install-recommends -y postgresql-client curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash --uid 10001 sentinel

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
# The catalogue and the schema are read-only reference data, owned by root and
# readable by the application user: the process that reads them cannot edit
# them, which is the point.
COPY --chown=root:root catalogue ./catalogue
COPY --chown=root:root sql ./sql
COPY --chown=root:root README.md LICENSE pyproject.toml ./
RUN mkdir -p /app/data/exports /app/data/evidence \
    && chown -R sentinel:sentinel /app/data

USER sentinel

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD sentinel doctor > /dev/null 2>&1 || exit 1

ENTRYPOINT ["sentinel"]
CMD ["--help"]
