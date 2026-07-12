# 部署骨架 v1 — Docker 迭代优先设计 Spec

## Why

项目 deadline 7月15日，部署 + 文档（D13-D15）分两段做：
- **现在（6/30 - 7/9）**：搭 Docker 骨架，不依赖代码定型，能早做。验证镜像可 build、可起、热重载可用，给后续留缓冲。
- **后续（7/10 - 7/14）**：README.md / start.bat / docs/demo-*.md，等代码定型后做，避免反复返工。

本 spec 只覆盖第一段（Docker 骨架）。README / demo / start.bat 留待后续 spec。

## 设计原则

**迭代优先 > 功能完整**。镜像要小、rebuild 要快、改代码不用重 build。具体：
- OCR 依赖（paddleocr/paddlepaddle ~1.5GB）作为可选 `BUILD_ARG`，默认不含
- compose 提供 `dev` / `prod` 两个 profile：dev 前端热重载 volume 挂载，prod 前端 build 嵌后端
- 数据目录全部 volume 挂载，镜像本身无状态

## What Changes

### 新增文件
- `Dockerfile` — 多阶段构建，前端 build + 后端运行时，OCR 可选层
- `docker-compose.yml` — dev/prod 双 profile，数据 volume 持久化
- `.dockerignore` — 排除 node_modules/.git/data/scripts/audit 等
- `backend/requirements-ocr.txt` — 拆出 paddleocr/paddlepaddle（可选安装）

### 修改文件
- 无（不动现有代码，骨架阶段不改 main.py / settings.py）

### 不在本 spec 范围
- ❌ README.md（等代码定型）
- ❌ start.bat（等 README 定型）
- ❌ docs/demo-*.md（等 UI 稳定）
- ❌ 前端构建产物嵌入后端的 StaticFiles 挂载（prod profile 才需要，但本次只搭骨架 + 验证 dev profile，prod profile 的 StaticFiles 挂载留到后续完善）

## Impact

- **Affected specs**: 无（新独立功能）
- **Affected code**: 不改业务代码。Dockerfile 引用 `backend/requirements.txt`、`backend/app/main.py:app`、`frontend/package.json`。后续若改入口路径或数据目录，需同步 Dockerfile。
- **关键路径约束**:
  - `backend/src/config/settings.py` 的 `ROOT_DIR = backend/`，所有数据路径（`data/chroma`、`data/chat_history.db`、`data/uploads`、`data/builtin_kb`、`data/bm25`）实际指向 `backend/data/`
  - `backend/src/rag/kb_manager.py` 的 `_BACKEND_DIR = backend/`，`BUILTIN_KB_DIR = backend/data/builtin_kb`（当前不存在，`ensure_builtin_kb()` 会优雅跳过）
  - 后端入口：`python -m app.main`，工作目录 `backend/`
  - `.env` 实际位置：`backend/.env`（settings.py 的 `env_file=ROOT_DIR / ".env"`，ROOT_DIR=backend/）。根目录的 `.env.example` 是文档样例，实际运行读 `backend/.env`
  - 默认监听 `0.0.0.0:8000`（settings.host 默认 0.0.0.0，容器内友好）
  - ChromaDB 默认嵌入式（`CHROMA_MODE=persistent`），无外部服务依赖
  - **已知端口矛盾**：`vite.config.ts` 的 proxy target 硬编码 `http://127.0.0.1:58080`，但 `dev.ps1` 后端起在 8000——前端 /api/* 代理会失败。这是项目已有 bug，不在本 spec 修复范围。dev profile 后端容器监听 58080 对齐 vite proxy，prod profile 监听 8000 标准端口

## ADDED Requirements

### Requirement: Dockerfile 多阶段构建

系统 SHALL 提供一个多阶段 Dockerfile：
- **Stage 1（前端构建）**：基于 `node:20-alpine`，`npm ci && npm run build`，产出 `frontend/dist/`
- **Stage 2（后端运行时）**：基于 `python:3.11-slim`，安装系统依赖（PyMuPDF 需要 libmupdf-dev、libjpeg-dev、zlib1g-dev、libgomp1）、Python 依赖（`pip install -r requirements.txt`），复制后端代码
- **OCR 可选层**：通过 `ARG INSTALL_OCR=false` 控制，为 true 时额外 `apt-get install libstdc++6` + `pip install -r requirements-ocr.txt`（paddlepaddle 运行时需要 libstdc++6）
- **工作目录**：`/app/backend`，入口 `python -m app.main`
- **暴露端口**：8000

#### Scenario: 默认构建（不含 OCR）
- **WHEN** 执行 `docker build -t hwrag .`
- **THEN** 镜像构建成功，大小约 1.5-2GB（不含 paddleocr）
- **AND** 镜像内 `python -c "import fitz; import chromadb; import fastapi"` 全部成功

#### Scenario: 含 OCR 构建
- **WHEN** 执行 `docker build --build-arg INSTALL_OCR=true -t hwrag-ocr .`
- **THEN** 镜像构建成功，大小约 3-3.5GB
- **AND** 镜像内 `python -c "import paddleocr"` 成功

### Requirement: docker-compose 双 profile

系统 SHALL 提供 `docker-compose.yml`，支持两个 profile：

#### dev profile（开发迭代 — 仅后端容器，监听 58080 对齐 vite proxy）
- 后端容器：volume 挂载 `backend/` 到 `/app/backend`，`uvicorn --reload` 热重载，监听 `58080`（对齐 `vite.config.ts` proxy target，开发者用 dev.ps1 跑前端可无缝配合）
- `.env` 挂载：`./backend/.env:/app/backend/.env:ro`
- 数据 volume 挂载 `backend/data/` 持久化
- 前端开发方式不在本 spec 范围（开发者可用 `scripts/dev.ps1` 或其他方式，dev profile 只负责后端热重载）

#### prod profile（生产单容器，监听 8000 标准端口）
- 单容器：后端 + 前端构建产物（前端 dist 复制到镜像，后续 spec 完善 StaticFiles 挂载）
- 端口映射 `127.0.0.1:8000:8000`（只暴露本地）
- 数据 volume 挂载 `./backend/data:/app/backend/data`
- `.env` 文件挂载 `./backend/.env:/app/backend/.env:ro` 只读

#### Scenario: dev 模式启动
- **WHEN** 执行 `docker compose --profile dev up -d`
- **THEN** 后端容器在 `http://127.0.0.1:58080` 起来，`/health` 返回 `{"status":"healthy"}`
- **AND** 修改 `backend/app/main.py` 后 uvicorn 自动 reload
- **AND** 开发者另开终端运行 `scripts/dev.ps1`（或 `npx vite`），vite proxy 到 `127.0.0.1:58080` 正常工作（对齐 vite.config.ts 现有配置）

#### Scenario: prod 模式启动
- **WHEN** 执行 `docker compose --profile prod up`
- **THEN** 单容器在 `http://127.0.0.1:8000` 起来
- **AND** `/health` 返回 healthy
- **AND** `/api/*` 走后端 API
- **AND** 前端静态文件由后端提供（本阶段 prod 只验证 API 可用，前端 StaticFiles 挂载留后续完善）

#### Scenario: 数据持久化
- **WHEN** 容器重启后
- **THEN** `backend/data/chroma/` 向量库保留
- **AND** `backend/data/chat_history.db` 会话历史保留
- **AND** `backend/data/uploads/` 上传文件保留
- **AND** `backend/data/bm25/` BM25 索引保留

### Requirement: .dockerignore 排除无关文件

系统 SHALL 提供 `.dockerignore`，排除：
- `node_modules/` / `frontend/node_modules/` / `frontend/dist/`（前端 dist 在 Stage 1 重新 build）
- `.git/` / `.vscode/` / `.idea/` / `.trae/` / `.claude/` / `.codegraph/`
- `data/`（项目根的 data，与 backend/data 不同，避免混淆）
- `backend/data/`（整个运行时数据目录，含 chroma/chroma_db/embedding_cache/test_results/uploads/bm25 及所有 .db 文件）
- `!backend/data/builtin_kb/`（白名单保留，若未来创建内置 KB 需进镜像；当前不存在不影响构建）
- `backend/.env`（含 API key 等敏感信息，不进镜像；运行时通过 compose 挂载）
- `backend/__pycache__/` / `**/__pycache__/` / `*.pyc`
- `backend/.deepeval/` / `backend/_*.py` / `backend/_*.json`（临时调试文件）
- `scripts/audit/` / `scripts/output_opendataloader/`（调试输出）
- `docs/`（文档不进镜像）
- `*.log` / `*.tmp` / `backend_dev.log` / `frontend_dev.log`
- `111.txt` / `tmp_*.py` / `fix-git-path.*`

#### Scenario: 构建上下文精简
- **WHEN** 执行 `docker build .`
- **THEN** 构建上下文小于 100MB（排除 data/、node_modules/、.git/ 后）

### Requirement: requirements-ocr.txt 拆分

系统 SHALL 将 OCR 相关依赖拆到独立文件 `backend/requirements-ocr.txt`：
```
paddleocr>=2.7
paddlepaddle>=2.6
```
原 `backend/requirements.txt` 删除这两行，其余不动。

#### Scenario: 默认安装不含 OCR
- **WHEN** `pip install -r requirements.txt`
- **THEN** paddleocr/paddlepaddle 未安装
- **AND** `python -c "import paddleocr"` 失败（预期行为）

#### Scenario: 显式安装 OCR
- **WHEN** `pip install -r requirements.txt && pip install -r requirements-ocr.txt`
- **THEN** paddleocr/paddlepaddle 安装成功

## 风险与已知限制

1. **reranker 模型首次下载**：bge-reranker-base ~280MB，首次启动从 HuggingFace 下载（reranker.py 已设 `HF_ENDPOINT=https://hf-mirror.com` 中国镜像）。compose 挂载 `/root/.cache/huggingface` volume 持久化，避免每次重启重下。
2. **PyMuPDF 系统依赖**：`python:3.11-slim` 需要额外装 `libmupdf-dev`、`libjpeg-dev`、`zlib1g-dev`、`libgomp1`。Dockerfile 要先 `apt-get install`。
3. **paddleocr 在 slim 镜像缺 libstdc++6**：含 OCR 构建时必须 `apt-get install libstdc++6`，否则 paddlepaddle import 时报 `ImportError: libstdc++.so.6`。
4. **prod profile 前端 StaticFiles 挂载未实现**：本 spec 只验证 prod 单容器 API 可用，前端静态文件托管（后端挂载 StaticFiles）留待后续 spec。当前 prod 模式下前端访问会 404，这是已知限制。Dockerfile 仍复制 `frontend/dist` 到镜像，为后续 spec 预留。
5. **builtin_kb 当前不存在**：`backend/data/builtin_kb/` 当前不存在，`ensure_builtin_kb()` 启动时检测到不存在会优雅跳过（`logger.info("Builtin KB path not found, skipping creation")`，return None）。未来若创建内置 KB，`.dockerignore` 白名单 `!backend/data/builtin_kb/` 会让它进镜像。
6. **dev profile 端口 58080 对齐 vite proxy**：dev profile 后端容器监听 58080（对齐 `vite.config.ts` proxy target），prod profile 监听 8000。两个 profile 端口不同是故意的，避免与本地 dev.ps1 后端（8000）冲突，同时让 vite proxy 无需改动即可配合 dev profile。
7. **容器以 root 用户运行**：`python:3.11-slim` 默认 root，HF cache 在 `/root/.cache/huggingface`。未创建非 root 用户（YAGNI，本地自部署场景安全风险低）。

## 不做的事（YAGNI）

- ❌ ChromaDB server 独立容器（项目默认嵌入式，未来上云再加）
- ❌ nginx 反代（单容器不需要）
- ❌ GitHub Actions CI（不在 D13-D15 范围）
- ❌ 健康检查探针（`/health` 端点已存在，compose healthcheck 可后续加）
- ❌ 多架构构建（amd64 够用，arm64 留后续）
