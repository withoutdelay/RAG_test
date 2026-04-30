ARG NODE_BASE_IMAGE=node:20-alpine
FROM ${NODE_BASE_IMAGE}

WORKDIR /app

ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1
ARG NEXT_PUBLIC_AUTH_ENABLED=false
ARG NEXT_PUBLIC_AUTH_COOKIE_NAME=presale_session
ARG NPM_REGISTRY=

ENV NEXT_TELEMETRY_DISABLED=1
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
ENV NEXT_PUBLIC_AUTH_ENABLED=${NEXT_PUBLIC_AUTH_ENABLED}
ENV NEXT_PUBLIC_AUTH_COOKIE_NAME=${NEXT_PUBLIC_AUTH_COOKIE_NAME}

COPY package.json package-lock.json ./
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi && npm ci --include=dev

COPY . .
RUN npm run build && npm prune --omit=dev

ENV NODE_ENV=production

EXPOSE 3000

CMD ["npm", "run", "start", "--", "--hostname", "0.0.0.0", "--port", "3000"]
