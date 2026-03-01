FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /workspace
COPY apps/router/pyproject.toml apps/router/uv.lock apps/router/
COPY shared/ shared/
WORKDIR /workspace/apps/router
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
RUN useradd -m -u 1001 appuser
WORKDIR /app
COPY --from=builder /workspace/apps/router/.venv /app/.venv
COPY apps/router/src/ ./src/
ENV PATH="/app/.venv/bin:$PATH"
USER appuser
EXPOSE 8002
CMD ["uvicorn", "router.main:app", "--host", "0.0.0.0", "--port", "8002"]
