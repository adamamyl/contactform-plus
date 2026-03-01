FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /workspace
COPY apps/jambonz/pyproject.toml apps/jambonz/uv.lock apps/jambonz/
COPY shared/ shared/
WORKDIR /workspace/apps/jambonz
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
RUN useradd -m -u 1001 appuser
WORKDIR /app
COPY --from=builder /workspace/apps/jambonz/.venv /app/.venv
COPY apps/jambonz/src/ ./src/
ENV PATH="/app/.venv/bin:$PATH"
USER appuser
EXPOSE 8004
CMD ["uvicorn", "jambonz.main:app", "--host", "0.0.0.0", "--port", "8004"]
