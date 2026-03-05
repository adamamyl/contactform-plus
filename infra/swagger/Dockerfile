FROM python:3.12-slim

WORKDIR /app

ARG SWAGGER_UI_VERSION=5.18.2

RUN apt-get update && apt-get install -y --no-install-recommends curl tar \
    && mkdir -p swagger_ui_dist \
    && curl -fsSL \
       "https://github.com/swagger-api/swagger-ui/archive/refs/tags/v${SWAGGER_UI_VERSION}.tar.gz" \
       | tar xz --strip-components=2 -C swagger_ui_dist \
         "swagger-ui-${SWAGGER_UI_VERSION}/dist/swagger-ui-bundle.js" \
         "swagger-ui-${SWAGGER_UI_VERSION}/dist/swagger-ui.css" \
    && apt-get remove -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

COPY pyproject.toml uv.lock .
RUN uv sync --frozen --no-dev

COPY app.py .

EXPOSE 8080
CMD ["uv", "run", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
