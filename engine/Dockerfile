FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=America/New_York

WORKDIR /srv/engine

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

# prompt-library is mounted read-only at runtime (see docker-compose.yml) so templates stay in git.
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
