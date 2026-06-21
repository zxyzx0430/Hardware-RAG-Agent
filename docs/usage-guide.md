# Hardware RAG Agent 快速上手指南

本文档面向第一次使用本项目的新用户，帮助你从 0 到 1 跑通前后端，并完成一次聊天与硬件工作台体验。

---

## 1. 环境准备

| 依赖 | 版本要求 | 说明 |
| --- | --- | --- |
| Python | 3.10+ | 后端运行环境 |
| Node.js | 18+ | 前端构建与开发服务器 |
| Git | 任意 | 克隆仓库 |
| ChromaDB | 可选 | 知识库向量化存储；不安装则上传功能仅保存文件并返回 chunks 数量 |

**推荐环境**

- Windows 10/11、macOS 或 Linux
- 已安装 `pip` 和 `npm`
- 有一个可用的 OpenAI 兼容 API Key（OpenAI、DeepSeek、Ollama 等均可）

---

## 2. 克隆仓库

```bash
git clone <仓库地址>
cd Hardware-RAG-Agent
```

---

## 3. 启动后端

进入后端目录，安装依赖并启动服务：

```bash
cd backend
pip install -r requirements.txt
python main.py
```

> 提示：项目当前主要入口为 `backend/app/main.py`。如果你发现 `python main.py` 进入的是 CLI 模式，请改用：
> ```bash
> python -m app.main
> ```

启动成功后，你会看到：

```
🌐 启动 Hardware RAG Agent API: http://0.0.0.0:8000
📖 API 文档: http://0.0.0.0:8000/docs
```

后端默认监听 `http://127.0.0.1:8000`。

### 3.1 配置环境变量（可选）

复制项目根目录的 `.env.example` 为 `.env`，并填写你的 API Key：

```bash
# .env
LLM_API_KEY=sk-your-api-key-here
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=4096
```

> 即使不配置 `.env`，也可以在前端 Settings 页面动态填写 API Key、Base URL 和 Model。

---

## 4. 启动前端

在另一个终端中进入前端目录，安装依赖并启动开发服务器：

```bash
cd frontend
npm install
npm run dev
```

启动成功后，打开浏览器访问：

```
http://localhost:5173
```

前端 Vite 代理会把所有 `/api` 请求转发到 `http://127.0.0.1:8000`，因此无需额外配置跨域。

---

## 5. 配置模型

1. 点击左上角或侧边栏的 **Settings（设置）** 图标。
2. 填写以下三项：
   - **API Key**：你的模型 API Key
   - **Base URL**：API 基础地址，例如 `https://api.openai.com/v1`、`https://api.deepseek.com/v1`、`http://localhost:11434/v1`（Ollama）
   - **Model**：模型 ID，例如 `gpt-4o`、`deepseek-chat`、`deepseek-r1`、`qwq-32b`
3. 点击保存。前端会自动调用 `/api/models` 获取模型列表并校验连通性。

> 如果你使用 Ollama 运行本地推理模型，请确保 Ollama 服务已启动，并且模型名称正确。

---

## 6. 开始聊天

1. 在底部输入框输入问题，例如：
   - "ESP32 的 I2C 总线需要上拉电阻吗？"
   - "STM32F103 的 GPIO0 有什么注意事项？"
2. 按 **Enter** 或点击发送按钮。
3. 聊天区域会实时显示三类 SSE 事件：
   - **thinking**：模型思考过程 / RAG 检索状态
   - **source**：检索到的知识库来源片段
   - **text**：模型逐 token 输出的正文
   - **done**：流式结束，显示最终状态

> 如果使用的是普通模型（如 GPT-4o），后端会自动发送 "正在生成回答..." 占位 thinking 事件，确保思考卡片始终可见。

---

## 7. 硬件工作台

点击侧边栏的 **Workbench（硬件工作台）** 图标，进入硬件开发辅助界面。工作台包含五个核心流程：

### 7.1 代码预览（Preview）

- 在编辑器中输入或粘贴 Arduino / PlatformIO 代码。
- 点击 **Diagnose（诊断）**，后端会调用 `/api/diagnose` 对代码做静态扫描：
  - GPIO 安全检查
  - 编译预检
  - 引脚冲突检测
  - 内存估算
  - Flash 兼容性
- 诊断结果按 `PASS` / `WARN` / `FAIL` 展示。

### 7.2 接线图（Wiring）

- 输入目标器件和连接关系，点击生成接线图。
- 后端调用 `/api/wiring` 返回 SVG 图像。
- 支持缩放、平移，并显示 BOM（物料清单）。

### 7.3 编译（Build）

- 选择目标环境（如 `esp32-s3-devkitc-1`）。
- 点击 **Build**，前端通过 `apiSSE('build', ...)` 连接后端 `/api/build`。
- 实时查看编译日志和进度条。

### 7.4 烧录（Flash）

- 选择串口（如 `COM3`）和目标环境。
- 点击 **Flash**，前端通过 `apiSSE('upload', ...)` 连接后端 `/api/upload`。
- 烧录完成后会显示成功或失败日志。

### 7.5 串口监视器（Serial）

- 选择串口和波特率（默认 115200），点击连接。
- 通过 WebSocket `/api/monitor/{port}?baud=115200` 实时接收设备输出。
- 支持发送文本、过滤日志、导出记录、控制 DTR / RTS。

### 7.6 安全审计（Audit Pins）

- 输入芯片型号和引脚分配。
- 点击 **Audit**，后端调用 `/api/audit_pins` 检查引脚冲突、Strapping 风险等。

> 硬件工作台涉及真实串口和烧录操作，请确保已连接目标设备，并在安全环境下测试。

---

## 8. 知识库上传

1. 切换到 **Knowledge（知识库）** 面板。
2. 点击上传，选择 PDF、Markdown 或 TXT 文件（最大 50 MB）。
3. 后端会保存文件，并返回切分后的 chunks 数量。

> 当前版本知识库上传后**仅保存文件并返回 chunks 数量**。完整向量化检索功能需要配置 Embedding API Key 和 ChromaDB 持久化目录，后续版本会默认启用。

---

## 9. 常见问题

### Q1: 前端提示"后端未连接"或请求失败

- 确认后端已启动：`http://127.0.0.1:8000/health` 应返回 `{"status":"healthy"}`。
- 确认前端 `vite.config.ts` 中的代理目标与后端端口一致（当前为 `http://127.0.0.1:8000`）。
- 检查防火墙是否拦截了 8000 或 5173 端口。

### Q2: 模型列表获取失败

- 检查 Settings 中的 **API Key** 和 **Base URL** 是否填写正确。
- 确认 Base URL 以 `/v1` 结尾（如 `https://api.openai.com/v1`）。
- 查看浏览器控制台 Network 面板，确认请求走的是 `/api/models`（不要再直接请求远程 API，避免 CORS 错误）。

### Q3: 发送消息后没有流式输出

- 检查模型是否支持流式返回。
- 如果使用 DeepSeek-R1 / QwQ / Ollama 推理模型，确保 Base URL 正确且模型支持 `reasoning_content` / `thinking` 字段。
- 查看后端日志，确认 `/api/chat` 是否正常接收请求。

### Q4: 串口无数据

- 确认设备已连接，并在操作系统设备管理器中识别到对应 COM 口。
- 确认波特率与设备一致（常见 115200、9600）。
- 检查是否有其他程序占用了该串口（如 Arduino IDE、PlatformIO 的 Serial Monitor）。
- 后端需要对应串口读写权限；Linux/macOS 用户可能需要将当前用户加入 `dialout` 组。

### Q5: 编译 / 烧录报错

- 确认已安装对应平台的工具链（如 `platformio`）。
- 查看 SSE 返回的完整错误日志，通常会给出具体文件和行号。
- 确认 `project_dir` 或环境名称与后端期望一致。

---

## 10. 下一步

- 阅读 [api-contract.md](./api-contract.md) 了解前后端接口契约。
- 阅读 [architecture.md](./architecture.md) 了解系统架构与数据流。
- 遇到错误先查阅 [pitfalls.md](./pitfalls.md) 中的踩坑记录。
