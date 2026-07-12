# syntax=docker/dockerfile:1

# ============================================================
# Stage 1: 前端构建（builder）
# ============================================================
FROM node:20-alpine AS builder

WORKDIR /app/frontend

# 先复制依赖清单，利用 Docker 层缓存
COPY frontend/package.json frontend/package-lock.json ./

# 安装依赖（严格按 lockfile）
RUN npm ci

# 复制前端源码
COPY frontend/ ./

# 构建生产产物
RUN npm run build

# 产物路径：/app/frontend/dist

# ============================================================
# Stage 2: 后端运行时
# ============================================================
FROM python:3.11-slim

# 系统依赖（PyMuPDF / 图像处理 / OpenBLAS 所需）
RUN apt-get update && apt-get install -y --no-install-recommends \
        libmupdf-dev \
        libjpeg-dev \
        zlib1g-dev \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

# 先复制依赖清单，利用 Docker 层缓存
COPY backend/requirements.txt backend/requirements-ocr.txt ./

# 安装后端核心依赖
RUN pip install --no-cache-dir -r requirements.txt

# OCR 可选层（默认不装，构建时 --build-arg INSTALL_OCR=true 启用）
ARG INSTALL_OCR=false
RUN if [ "$INSTALL_OCR" = "true" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends libstdc++6 \
        && rm -rf /var/lib/apt/lists/* \
        && pip install --no-cache-dir -r requirements-ocr.txt; \
    fi

# 复制后端源码
COPY backend/ ./

# 复制前端构建产物
COPY --from=builder /app/frontend/dist /app/frontend/dist

# 环境变量默认值
ENV HOST=0.0.0.0 \
    PORT=8000 \
    CHROMA_MODE=persistent \
    PYTHONUNBUFFERED=1 \
    HF_ENDPOINT=https://hf-mirror.com

EXPOSE 8000

CMD ["python", "-m", "app.main"]
