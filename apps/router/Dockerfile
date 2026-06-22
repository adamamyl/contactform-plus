FROM python:3.14-slim AS builder
RUN pip install uv==0.7.13 --no-cache-dir
WORKDIR /workspace
COPY apps/router/pyproject.toml apps/router/uv.lock apps/router/
COPY shared/ shared/
WORKDIR /workspace/apps/router
RUN UV_PROJECT_ENVIRONMENT=/app/.venv uv sync --frozen --no-dev --no-install-project

FROM python:3.14-slim
RUN useradd -m -u 1001 appuser
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY apps/router/src/ ./src/
COPY shared/src/ ./src/
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app/src
USER appuser
EXPOSE 8002
CMD ["uvicorn", "router.main:app", "--host", "0.0.0.0", "--port", "8002"]
