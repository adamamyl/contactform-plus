FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /workspace
COPY apps/form/pyproject.toml apps/form/uv.lock apps/form/
COPY shared/ shared/
WORKDIR /workspace/apps/form
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
RUN useradd -m -u 1001 appuser
WORKDIR /app
COPY --from=builder /workspace/apps/form/.venv /app/.venv
COPY apps/form/src/ ./src/
COPY apps/form/templates/ ./templates/
COPY apps/form/static/ ./static/
ENV PATH="/app/.venv/bin:$PATH"
USER appuser
EXPOSE 8000
CMD ["uvicorn", "emf_form.main:app", "--host", "0.0.0.0", "--port", "8000"]
