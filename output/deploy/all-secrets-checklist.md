# Secrets Checklist

## Runtime

- [ ] `SECRET_KEY`
- [ ] `DATABASE_URL`
- [ ] `REDIS_URL`
- [ ] `QDRANT_HOST`
- [ ] `QDRANT_PORT`
- [ ] `QDRANT_COLLECTION`
- [ ] `MINIO_ACCESS_KEY`
- [ ] `MINIO_SECRET_KEY`
- [ ] `MINIO_BUCKET`

## LLM Providers

- [ ] `OPENAI_API_KEY`
- [ ] `AZURE_OPENAI_API_KEY`
- [ ] `AZURE_OPENAI_ENDPOINT`
- [ ] `AZURE_OPENAI_DEPLOYMENT`
- [ ] `DEEPSEEK_API_KEY`
- [ ] `QWEN_API_KEY`
- [ ] `DOUBAO_API_KEY`

## Deployment Review

- [ ] Production `.env` generated from `.env.deploy.example`
- [ ] CI/CD secret store updated
- [ ] Staging secret store updated
- [ ] Secret rotation owner confirmed
