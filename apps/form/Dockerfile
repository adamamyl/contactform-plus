FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /workspace
COPY apps/form/pyproject.toml apps/form/uv.lock apps/form/
COPY shared/ shared/
WORKDIR /workspace/apps/form
RUN UV_PROJECT_ENVIRONMENT=/app/.venv uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim
RUN useradd -m -u 1001 appuser
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY apps/form/src/ ./src/
COPY shared/src/ ./src/
COPY apps/form/templates/ ./templates/
COPY apps/form/static/ ./static/
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app/src
USER appuser
EXPOSE 8000
CMD ["uvicorn", "emf_form.main:app", "--host", "0.0.0.0", "--port", "8000"]
