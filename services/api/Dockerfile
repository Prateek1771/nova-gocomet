# Build context: repo root.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages packages
COPY services/api services/api
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --package nova-api --no-editable

FROM python:3.13-slim-bookworm
RUN useradd --system --uid 10001 nova
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/services/api/alembic.ini /app/alembic.ini
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /app
USER nova
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz')"
CMD ["uvicorn", "nova_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
