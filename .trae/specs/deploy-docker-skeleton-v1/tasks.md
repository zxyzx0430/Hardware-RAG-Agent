# Tasks

部署骨架 v1 — Docker 迭代优先。本清单只覆盖 Docker 骨架，README/demo/start.bat 留待后续 spec。

- [x] Task 1: 创建 `.dockerignore`
  - [x] SubTask 1.1: 排除 node_modules / frontend/dist / .git / IDE 元数据
  - [x] SubTask 1.2: 排除 `backend/data/` 整个目录（含 chroma/chroma_db/embedding_cache/test_results/uploads/bm25 及所有 .db 文件），白名单 `!backend/data/builtin_kb/`（未来内置 KB 进镜像）
  - [x] SubTask 1.3: 排除临时调试文件（backend/_*.py、scripts/audit、scripts/output_opendataloader、111.txt、tmp_*.py、fix-git-path.*）
  - [x] SubTask 1.4: 排除日志和文档（*.log、*.tmp、docs/）
  - [x] SubTask 1.5: 排除 `backend/.env`（含 API key，不进镜像，运行时 compose 挂载）；保留 `backend/requirements.txt`、`backend/requirements-ocr.txt`、`frontend/package.json`、`frontend/package-lock.json`

- [x] Task 2: 拆分 `backend/requirements-ocr.txt`
  - [x] SubTask 2.1: 新建 `backend/requirements-ocr.txt`，内容为 `paddleocr>=2.7` + `paddlepaddle>=2.6`
  - [x] SubTask 2.2: 从 `backend/requirements.txt` 删除 paddleocr/paddlepaddle 两行（保留注释说明指向 requirements-ocr.txt）
  - [x] SubTask 2.3: 验证 `pip install -r requirements.txt` 不含 OCR 依赖

- [x] Task 3: 编写 `Dockerfile`（多阶段构建）
  - [x] SubTask 3.1: Stage 1 前端构建（node:20-alpine，npm ci && npm run build，产出 dist）
  - [x] SubTask 3.2: Stage 2 后端运行时基础（python:3.11-slim，apt-get install libmupdf-dev libjpeg-dev zlib1g-dev libgomp1）
  - [x] SubTask 3.3: Stage 2 安装 Python 依赖（COPY requirements.txt，pip install）
  - [x] SubTask 3.4: Stage 2 OCR 可选层（ARG INSTALL_OCR=false，为 true 时额外 apt-get install libstdc++6 + pip install -r requirements-ocr.txt）
  - [x] SubTask 3.5: Stage 2 复制后端代码（COPY backend/ /app/backend/）
  - [x] SubTask 3.6: Stage 2 复制前端构建产物（COPY --from=builder /app/frontend/dist /app/frontend/dist，为后续 prod StaticFiles 挂载预留）
  - [x] SubTask 3.7: 设置 WORKDIR=/app/backend、EXPOSE 8000、CMD ["python", "-m", "app.main"]
  - [x] SubTask 3.8: 设置环境变量默认值（HOST=0.0.0.0、PORT=8000、CHROMA_MODE=persistent、PYTHONUNBUFFERED=1、HF_ENDPOINT=https://hf-mirror.com）

- [x] Task 4: 编写 `docker-compose.yml`（dev/prod 双 profile，dev 仅后端容器监听 58080）
  - [x] SubTask 4.1: 定义命名 volume（hwrag_hf_cache）
  - [x] SubTask 4.2: dev profile 后端服务（build .，volume 挂载 ./backend:/app/backend，command uvicorn app.main:app --reload --host 0.0.0.0 --port 58080，端口映射 127.0.0.1:58080:58080，环境变量 PORT=58080）
  - [x] SubTask 4.3: dev profile .env 挂载（./backend/.env:/app/backend/.env:ro）
  - [x] SubTask 4.4: dev profile 数据 bind mount（./backend/data:/app/backend/data）
  - [x] SubTask 4.5: dev profile HF cache volume 挂载（/root/.cache/huggingface）
  - [x] SubTask 4.6: prod profile 单容器服务（build .，端口映射 127.0.0.1:8000:8000，环境变量 PORT=8000，数据 bind mount ./backend/data:/app/backend/data，.env 只读挂载 ./backend/.env:/app/backend/.env:ro）
  - [x] SubTask 4.7: prod profile HF cache volume 挂载

- [x] Task 5: 创建 `.env.docker.example`（Docker 专用环境变量样例）
  - [x] SubTask 5.1: 列出 Docker 运行需要的环境变量（LLM_API_KEY、EMBEDDING_API_KEY、HOST=0.0.0.0、PORT=8000、CHROMA_MODE=persistent、LOG_LEVEL=INFO）
  - [x] SubTask 5.2: 注释说明 dev/prod profile 差异和 OCR 构建参数

- [x] Task 6: 验证 Docker 构建（本地有 Docker，语法验证通过，实际 build 推迟到 7月10日）
  - [x] SubTask 6.1: 验证 `docker compose config` 语法正确（dev + prod 两个 profile 都能解析）— 三模式 config --quiet 全过
  - [ ] SubTask 6.2: 若本地有 Docker：执行 `docker build -t hwrag-test .` 验证默认构建成功 — 推迟到 7月10日
  - [ ] SubTask 6.3: 若本地有 Docker：执行 `docker compose --profile dev up -d` 验证后端 /health 返回 healthy — 推迟
  - [ ] SubTask 6.4: 若本地有 Docker：验证 dev 模式下修改 backend 代码触发 uvicorn reload — 推迟
  - [x] SubTask 6.5: 路径验证全部通过（7 个路径存在），文档已同步

- [x] Task 7: 更新文档（轻量，不写 README）
  - [x] SubTask 7.1: 在 `docs/completed.md` 追加"部署骨架 v1"记录（Dockerfile/compose/.dockerignore/requirements-ocr 拆分）
  - [x] SubTask 7.2: 在 `docs/todos/08-infra.md` 把 Docker 相关 TODO 标记为部分完成（[x] Dockerfile 骨架，[ ] 实际 build 验证留 7月10日）
  - [x] SubTask 7.3: 在 `docs/pitfalls.md` 记录踩坑（无踩坑，跳过）

# Task Dependencies

- Task 2 必须先于 Task 3（Dockerfile 引用 requirements-ocr.txt）✓
- Task 1 应先于 Task 3（.dockerignore 影响 build 上下文）✓
- Task 3 和 Task 4 可并行（Dockerfile 和 compose 独立编写）✓
- Task 5 依赖 Task 4（环境变量与 compose 配合）✓
- Task 6 依赖 Task 1-5 全部完成 ✓
- Task 7 依赖 Task 6 完成（记录验证结果）✓

# 后续 spec（不在本批次）

- README.md（7月10-14日，等代码定型）
- start.bat（7月10-14日，Docker 启动入口）
- docs/demo-*.md（7月10-14日，demo 走查脚本）
- prod profile 前端 StaticFiles 挂载（后续 spec，后端挂载 StaticFiles 托管前端 dist）
- 实际 build 验证（7月10日，本地有 Docker，已做语法验证）
