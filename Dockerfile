FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# dependencies first, so code changes don't reinstall them
COPY pyproject.toml requirements.txt uv.lock* README.md ./
RUN uv sync --no-install-project

COPY src ./src
COPY configs ./configs
RUN uv sync

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["uvicorn", "sadhana.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
