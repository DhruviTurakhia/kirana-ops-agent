FROM ghcr.io/astral-sh/uv:0.11.28 AS uv

FROM python:3.13-slim

COPY --from=uv /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    DATABASE_PATH=/app/data/kirana.sqlite3 \
    AGENT_SESSION_DATABASE_PATH=/app/data/agent_sessions.sqlite3 \
    ARTIFACT_OUTPUT_DIR=/app/output \
    STORE_TIMEZONE=Asia/Kolkata

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

RUN mkdir -p /app/data /app/output/pdf /app/output/pptx
VOLUME ["/app/data", "/app/output"]

CMD ["uv", "run", "--no-sync", "kirana-bot"]
