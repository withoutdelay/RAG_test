ARG PYTHON_BASE_IMAGE=python:3.11-slim
FROM ${PYTHON_BASE_IMAGE}

WORKDIR /app

ARG BACKEND_EXTRAS=

COPY app ./app
RUN mkdir -p \
    data/case_library \
    data/knowledge_wiki \
    data/pdf_audits \
    data/sample_manifests \
    data/uploads \
    data/visual_index
COPY data/case_library ./data/case_library
COPY pyproject.toml .
RUN if [ -n "$BACKEND_EXTRAS" ]; then pip install --no-cache-dir ".[$BACKEND_EXTRAS]"; else pip install --no-cache-dir .; fi

COPY alembic.ini .
COPY alembic ./alembic

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
