FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# XGBoost needs the OpenMP runtime, which the slim image leaves out
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app

# dependencies come from requirements.txt, so uv needs the source to build project metadata
COPY pyproject.toml requirements.txt uv.lock README.md ./
COPY src ./src
COPY configs ./configs
RUN uv sync --locked --no-editable

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["uvicorn", "sadhana.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
