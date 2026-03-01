FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /workspace
COPY apps/tts/pyproject.toml apps/tts/uv.lock apps/tts/
COPY shared/ shared/
WORKDIR /workspace/apps/tts
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
RUN useradd -m -u 1001 appuser
WORKDIR /app

ARG PIPER_VERSION=2023.11.14-2
ARG PIPER_ARCH=amd64
RUN apt-get update && apt-get install -y --no-install-recommends curl tar && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /app/piper /app/models && \
    curl -fsSL "https://github.com/rhasspy/piper/releases/download/${PIPER_VERSION}/piper_${PIPER_ARCH}.tar.gz" \
    | tar -xz -C /app/piper --strip-components=1 && \
    curl -fsSL "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx" \
    -o /app/models/en_GB-alan-medium.onnx && \
    curl -fsSL "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json" \
    -o /app/models/en_GB-alan-medium.onnx.json

COPY --from=builder /workspace/apps/tts/.venv /app/.venv
COPY apps/tts/src/ ./src/
ENV PATH="/app/.venv/bin:$PATH"
ENV PIPER_BIN=/app/piper/piper
ENV PIPER_MODEL=/app/models/en_GB-alan-medium.onnx
USER appuser
EXPOSE 8003
CMD ["uvicorn", "tts.main:app", "--host", "0.0.0.0", "--port", "8003"]
