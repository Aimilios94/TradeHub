# Build from the repo root: docker compose build api
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.2 /uv /bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /srv/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
# Dev tools (pytest, ruff) are included so the test suite can run inside the Compose network.
RUN uv sync --frozen

COPY backend/ /srv/backend/
COPY db/ /srv/db/

EXPOSE 8000
# Migrate, load config seeds, then serve. Any failure stops the container with the real error.
CMD ["sh", "-c", "alembic -c /srv/db/alembic.ini upgrade head && python -m scripts.load_seeds && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
