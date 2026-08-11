FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

WORKDIR /build

RUN python -m venv "${VIRTUAL_ENV}"

COPY pyproject.toml README.md ./
COPY atem_ai_vision_mixer ./atem_ai_vision_mixer

# Install every portable tier. Live capture is deliberately host-only.
RUN python -m pip install ".[core,perception,llm,dev]"


FROM python:3.11-slim AS test

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

WORKDIR /app

# Portable perception wheels need these small Linux runtime libraries.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY pyproject.toml ./
COPY tests ./tests

RUN python -m pytest \
    --cov=atem_ai_vision_mixer \
    --cov-report=term-missing

CMD ["python", "-m", "pytest", "--cov=atem_ai_vision_mixer", "--cov-report=term-missing"]
