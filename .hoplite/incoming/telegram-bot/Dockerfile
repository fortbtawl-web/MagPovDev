FROM node:22-alpine AS frontend
WORKDIR /app/frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile || pnpm install
COPY frontend/ ./
RUN pnpm build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY bot ./bot
COPY database ./database
COPY scripts ./scripts
COPY website ./website
COPY alembic.ini ./
COPY --from=frontend /app/frontend/dist ./frontend/dist
RUN useradd -r -u 10001 app && chown -R app /app
USER app
EXPOSE 8080
CMD ["python", "-m", "backend"]
