ARG PYTHON_BASE_IMAGE=python:3.11-slim
FROM ${PYTHON_BASE_IMAGE}

WORKDIR /app

ARG BACKEND_EXTRAS=
ARG INSTALL_CPU_TORCH=true
ARG TORCH_CPU_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG PIP_INDEX_URL=
ARG PIP_TRUSTED_HOST=
ARG INSTALL_LIBREOFFICE=true
ARG APT_MIRROR=
ARG HF_ENDPOINT=https://huggingface.co
ARG HF_HOME=/opt/huggingface
ARG DOCLING_CACHE_DIR=/opt/docling/cache
ARG DOCLING_ARTIFACTS_PATH=
ARG PREWARM_DOCLING_MODELS=false

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV HF_ENDPOINT=${HF_ENDPOINT}
ENV HF_HOME=${HF_HOME}
ENV TRANSFORMERS_CACHE=${HF_HOME}/transformers
ENV DOCLING_CACHE_DIR=${DOCLING_CACHE_DIR}

RUN if [ "${INSTALL_LIBREOFFICE}" = "true" ]; then \
      if [ -n "${APT_MIRROR}" ] && [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|http://deb.debian.org/debian|${APT_MIRROR}|g; s|http://deb.debian.org/debian-security|${APT_MIRROR}-security|g" /etc/apt/sources.list.d/debian.sources; \
      fi && \
      apt-get update && \
      apt-get install -y --no-install-recommends \
        libreoffice-writer \
        libgl1 \
        fontconfig \
        fonts-noto-cjk && \
      rm -rf /var/lib/apt/lists/*; \
    fi

RUN if [ -n "$PIP_INDEX_URL" ]; then pip config set global.index-url "$PIP_INDEX_URL"; fi && \
    if [ -n "$PIP_TRUSTED_HOST" ]; then pip config set global.trusted-host "$PIP_TRUSTED_HOST"; fi

COPY pyproject.toml .
RUN mkdir -p app && touch app/__init__.py
RUN case ",${BACKEND_EXTRAS}," in \
      *",parsing,"*|*",embeddings,"*|*",full,"*) \
        if [ "${INSTALL_CPU_TORCH}" = "true" ]; then \
          pip install --no-cache-dir filelock typing-extensions sympy networkx jinja2 fsspec && \
          pip install --no-cache-dir --no-deps --index-url "${TORCH_CPU_INDEX_URL}" torch torchvision; \
        fi \
        ;; \
    esac
RUN if [ -n "$BACKEND_EXTRAS" ]; then pip install --no-cache-dir ".[$BACKEND_EXTRAS]"; else pip install --no-cache-dir .; fi

RUN MODELS_DIR="${DOCLING_ARTIFACTS_PATH:-/opt/docling/models}" && \
    mkdir -p "${HF_HOME}" "${TRANSFORMERS_CACHE}" "${DOCLING_CACHE_DIR}" "${MODELS_DIR}" && \
    if [ "${PREWARM_DOCLING_MODELS}" = "true" ]; then \
      python -m docling.cli.models download --output-dir "${MODELS_DIR}" --quiet; \
    fi

COPY app ./app
RUN mkdir -p \
    data/case_library \
    data/knowledge_wiki \
    data/pdf_audits \
    data/sample_manifests \
    data/uploads \
    data/visual_index
COPY data/case_library ./data/case_library

COPY alembic.ini .
COPY alembic ./alembic

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
