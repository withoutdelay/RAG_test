FROM python:3.11-slim

WORKDIR /app

ARG BACKEND_EXTRAS=

COPY app ./app
COPY data/case_library ./data/case_library
COPY pyproject.toml .
RUN if [ -n "$BACKEND_EXTRAS" ]; then pip install --no-cache-dir ".[$BACKEND_EXTRAS]"; else pip install --no-cache-dir .; fi

COPY alembic.ini .
COPY alembic ./alembic

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
