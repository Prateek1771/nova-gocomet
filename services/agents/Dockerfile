# Build context: repo root.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages packages
COPY services/agents services/agents
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --package nova-agents --no-editable

FROM python:3.13-slim-bookworm
RUN useradd --system --uid 10001 nova
COPY --from=build /app/.venv /app/.venv
COPY definitions /app/definitions
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 DEFINITIONS_DIR=/app/definitions
USER nova
CMD ["python", "-m", "nova_agents.worker"]
