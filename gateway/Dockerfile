ARG PYTHON_BASE_IMAGE=python:3.11-slim
FROM ${PYTHON_BASE_IMAGE}

WORKDIR /app

ARG PIP_INDEX_URL=
ARG PIP_TRUSTED_HOST=

ENV PIP_DISABLE_PIP_VERSION_CHECK=1

RUN if [ -n "$PIP_INDEX_URL" ]; then pip config set global.index-url "$PIP_INDEX_URL"; fi && \
    if [ -n "$PIP_TRUSTED_HOST" ]; then pip config set global.trusted-host "$PIP_TRUSTED_HOST"; fi

RUN pip install --no-cache-dir fastapi uvicorn[standard] redis[hiredis] pydantic-settings presidio-analyzer presidio-anonymizer

COPY app ./app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
