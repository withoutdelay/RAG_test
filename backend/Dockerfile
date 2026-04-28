ARG PYTHON_BASE_IMAGE=python:3.11-slim
FROM ${PYTHON_BASE_IMAGE}

WORKDIR /app

ARG BACKEND_EXTRAS=
ARG INSTALL_CPU_TORCH=true
ARG TORCH_CPU_INDEX_URL=https://download.pytorch.org/whl/cpu

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
RUN case ",${BACKEND_EXTRAS}," in \
      *",embeddings,"*|*",full,"*) \
        if [ "${INSTALL_CPU_TORCH}" = "true" ]; then \
          pip install --no-cache-dir --index-url "${TORCH_CPU_INDEX_URL}" torch; \
        fi \
        ;; \
    esac
RUN if [ -n "$BACKEND_EXTRAS" ]; then pip install --no-cache-dir ".[$BACKEND_EXTRAS]"; else pip install --no-cache-dir .; fi

COPY alembic.ini .
COPY alembic ./alembic

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
