# Shared image for both backend services (ingest + api); compose supplies the
# per-service command, so no CMD here.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Deps first for build-cache friendliness
COPY pyproject.toml ./
RUN uv pip install --system -r pyproject.toml

COPY . .
RUN uv pip install --system --no-deps .

ENV PYTHONUNBUFFERED=1
