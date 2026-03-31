FROM python:3.12-slim

# Install ffmpeg for Opus encoding, system audio libs, and build deps for madmom
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg libsndfile1 \
        gcc g++ pkg-config && \
    rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
RUN uv sync --no-dev

# Copy source
COPY src/ src/

CMD ["uv", "run", "python", "-m", "src"]
