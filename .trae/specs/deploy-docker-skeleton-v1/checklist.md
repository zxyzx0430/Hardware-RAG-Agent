# Checklist

部署骨架 v1 验证清单。每项完成后打勾。

## 文件存在性

- [x] `Dockerfile` 存在于项目根目录
- [x] `docker-compose.yml` 存在于项目根目录
- [x] `.dockerignore` 存在于项目根目录
- [x] `backend/requirements-ocr.txt` 存在
- [x] `.env.docker.example` 存在于项目根目录

## .dockerignore 正确性

- [x] `.dockerignore` 排除了 `node_modules/`、`frontend/node_modules/`、`frontend/dist/`
- [x] `.dockerignore` 排除了 `.git/`、`.vscode/`、`.idea/`、`.trae/`、`.claude/`、`.codegraph/`
- [x] `.dockerignore` 排除了 `backend/data/` 整个目录（含 chroma/chroma_db/embedding_cache/test_results/uploads/bm25 及所有 .db 文件）
- [x] `.dockerignore` 白名单保留了 `!backend/data/builtin_kb/`（未来内置 KB 进镜像，当前不存在不影响）
- [x] `.dockerignore` 排除了 `backend/_*.py`、`backend/_*.json`、`scripts/audit/`、`scripts/output_opendataloader/`
- [x] `.dockerignore` 排除了 `*.log`、`*.tmp`、`docs/`、`111.txt`、`tmp_*.py`、`fix-git-path.*`
- [x] `.dockerignore` 排除了 `backend/.env`（含 API key，不进镜像，运行时 compose 挂载）
- [x] `.dockerignore` 保留了 `backend/requirements.txt`、`backend/requirements-ocr.txt`、`frontend/package.json`、`frontend/package-lock.json`

## requirements 拆分

- [x] `backend/requirements-ocr.txt` 包含 `paddleocr>=2.7` 和 `paddlepaddle>=2.6`
- [x] `backend/requirements.txt` 不再包含 paddleocr/paddlepaddle 两行
- [x] `backend/requirements.txt` 有注释说明 OCR 依赖在 `requirements-ocr.txt`
- [x] 其余依赖项原样保留（fastapi、chromadb、pymupdf 等 35+ 包都在）

## Dockerfile 正确性

- [x] 使用多阶段构建（Stage 1 node:20-alpine 前端构建，Stage 2 python:3.11-slim 后端运行时）
- [x] Stage 2 安装系统依赖：`libmupdf-dev`、`libjpeg-dev`、`zlib1g-dev`、`libgomp1`
- [x] Stage 2 `pip install -r requirements.txt`（不含 OCR）
- [x] `ARG INSTALL_OCR=false` 控制可选 OCR 层
- [x] OCR 层为 true 时额外 `apt-get install libstdc++6` + `pip install -r requirements-ocr.txt`
- [x] `WORKDIR /app/backend`
- [x] `EXPOSE 8000`
- [x] `CMD ["python", "-m", "app.main"]`
- [x] 环境变量默认值：`HOST=0.0.0.0`、`PORT=8000`、`CHROMA_MODE=persistent`、`PYTHONUNBUFFERED=1`、`HF_ENDPOINT=https://hf-mirror.com`
- [x] 前端构建产物 `frontend/dist/` 复制到镜像内 `/app/frontend/dist`（为后续 prod StaticFiles 挂载预留）
- [x] 后端代码 `backend/` 复制到 `/app/backend/`

## docker-compose.yml 正确性

- [x] 定义命名 volume `hwrag_hf_cache`
- [x] dev profile 后端服务：build .、volume 挂载 `./backend:/app/backend`、command `uvicorn app.main:app --reload --host 0.0.0.0 --port 58080`、端口映射 `127.0.0.1:58080:58080`、环境变量 `PORT=58080`
- [x] dev profile `.env` 挂载：`./backend/.env:/app/backend/.env:ro`
- [x] dev profile 数据 bind mount：`./backend/data:/app/backend/data`（通过 `./backend:/app/backend` 整体挂载覆盖）
- [x] dev profile HF cache volume 挂载到 `/root/.cache/huggingface`
- [x] prod profile 单容器：build .、端口映射 `127.0.0.1:8000:8000`、环境变量 `PORT=8000`、数据 bind mount `./backend/data:/app/backend/data`、`.env` 只读挂载 `./backend/.env:/app/backend/.env:ro`
- [x] prod profile HF cache volume 挂载
- [x] `docker compose config` 语法验证通过（dev 和 prod 两个 profile 都能解析）— 三模式 config --quiet 全过

## .env.docker.example 正确性

- [x] 包含 `LLM_API_KEY=`、`LLM_BASE_URL=`、`LLM_MODEL=` 占位符
- [x] 包含 `EMBEDDING_API_KEY=`、`EMBEDDING_BASE_URL=`、`EMBEDDING_MODEL=` 占位符
- [x] 包含 `HOST=0.0.0.0`、`PORT=8000`、`CHROMA_MODE=persistent`、`LOG_LEVEL=INFO`、`HF_ENDPOINT=https://hf-mirror.com`
- [x] 注释说明 dev profile 用 PORT=58080、prod profile 用 PORT=8000
- [x] 注释说明 OCR 构建参数 `--build-arg INSTALL_OCR=true`
- [x] 注释说明实际 .env 应放在 `backend/.env`（对齐 settings.py 的 ROOT_DIR）

## 语法与路径验证

- [x] `docker compose config` 命令执行无语法错误（本地有 Docker，三模式 config --quiet 全过：default/dev/prod）
- [x] Dockerfile 中引用的所有路径（`backend/requirements.txt`、`backend/app/main.py`、`frontend/package.json`）在项目中实际存在
- [x] docker-compose.yml 中 volume 挂载的源路径（`./backend`、`./backend/data`、`./backend/.env`）在项目中实际存在
- [x] Dockerfile 的 `CMD ["python", "-m", "app.main"]` 与项目入口一致（`backend/app/main.py` 有 `app = create_app()`）
- [x] 确认 `backend/.env` 实际存在
- [x] 确认 `backend/data/` 实际存在且有数据（含 chroma/chroma_db/embedding_cache/test_results/uploads/bm25 及多个 .db 文件）
- [x] 确认 `backend/data/builtin_kb/` 当前不存在（ensure_builtin_kb 会优雅跳过）

## 实际 build 验证（本地有 Docker，按 spec 要求推迟到 7月10日）

> 本地有 Docker（version 29.3.1, compose v5.1.1），已通过 `docker compose config --quiet` 三模式语法验证。实际 build/up 按原计划推迟到 7月10日，避免当前迭代阶段耗时。

- [ ] `docker build -t hwrag-test .` 构建成功（默认不含 OCR）— 推迟到 7月10日
- [ ] 镜像内 `docker run --rm hwrag-test python -c "import fitz; import chromadb; import fastapi"` 成功 — 推迟
- [ ] `docker build --build-arg INSTALL_OCR=true -t hwrag-ocr-test .` 构建成功（含 OCR）— 推迟
- [ ] 镜像内 `docker run --rm hwrag-ocr-test python -c "import paddleocr"` 成功 — 推迟
- [ ] `docker compose --profile dev up -d` 启动成功（仅后端容器）— 推迟
- [ ] `curl http://127.0.0.1:58080/health` 返回 `{"status":"healthy"}` — 推迟
- [ ] 修改 `backend/app/main.py` 后 uvicorn 自动 reload — 推迟
- [ ] `docker compose --profile dev down` 清理成功 — 推迟
- [ ] `docker compose --profile prod up -d` 启动成功 — 推迟
- [ ] `curl http://127.0.0.1:8000/health` 返回 healthy — 推迟
- [ ] `curl http://127.0.0.1:8000/api/models` 返回 JSON — 推迟
- [ ] 容器重启后 `backend/data/chroma/` 数据保留 — 推迟

## 文档同步

- [x] `docs/completed.md` 追加"部署骨架 v1"记录
- [x] `docs/todos/08-infra.md` Docker 相关 TODO 标记部分完成
- [x] `docs/pitfalls.md` 记录踩坑（若有）— 无踩坑，跳过

## 已知限制（本 spec 不解决，记录在案）

- [x] prod profile 前端静态文件托管未实现（前端访问 404，后续 spec 加 StaticFiles 挂载）
- [x] 实际 build 验证推迟到 7月10日（本地有 Docker，已做语法验证）
- [x] OCR 构建未在 CI 验证（本 spec 无 CI）
