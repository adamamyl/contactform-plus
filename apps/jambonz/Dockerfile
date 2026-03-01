FROM python:3.12-slim AS builder
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
COPY ../../shared /shared
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
RUN useradd -m -u 1001 appuser
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY src/ ./src/
ENV PATH="/app/.venv/bin:$PATH"
USER appuser
EXPOSE 8004
CMD ["uvicorn", "jambonz.main:app", "--host", "0.0.0.0", "--port", "8004"]
