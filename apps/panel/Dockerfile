FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /workspace
COPY apps/panel/pyproject.toml apps/panel/uv.lock apps/panel/
COPY shared/ shared/
WORKDIR /workspace/apps/panel
RUN UV_PROJECT_ENVIRONMENT=/app/.venv uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim
RUN useradd -m -u 1001 appuser
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY apps/panel/src/ ./src/
COPY shared/src/ ./src/
COPY apps/panel/templates/ ./templates/
COPY apps/panel/static/ ./static/
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app/src
USER appuser
EXPOSE 8001
CMD ["uvicorn", "emf_panel.main:app", "--host", "0.0.0.0", "--port", "8001", "--proxy-headers", "--forwarded-allow-ips=*"]
