ARG PYTHON_BASE_IMAGE=python:3.11-slim
FROM ${PYTHON_BASE_IMAGE}

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn[standard] redis[hiredis] pydantic-settings presidio-analyzer presidio-anonymizer

COPY app ./app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
