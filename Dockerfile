FROM node:22-slim AS frontend

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

COPY index.html tsconfig.json tsconfig.app.json tsconfig.node.json vite.config.ts ./
COPY public ./public
COPY src ./src
RUN npm run build


FROM python:3.12-slim AS runtime

RUN useradd --create-home --uid 1000 user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860

USER user
WORKDIR /home/user/app

COPY --chown=user requirements.txt ./
RUN pip install --user --no-cache-dir --upgrade pip && \
    pip install --user --no-cache-dir -r requirements.txt

COPY --chown=user backend ./backend
COPY --chown=user data ./data
COPY --chown=user public/iitgn-logo.png ./public/iitgn-logo.png
COPY --from=frontend --chown=user /app/dist ./dist

EXPOSE 7860
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "7860", "--proxy-headers", "--forwarded-allow-ips", "*"]
