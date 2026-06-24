# Week 1 代码导读

这份文档不是站在“会写的人”角度写的，而是站在“我要真看懂这一周到底做了什么”来写的。

目标只有一个：  
**让你能按 Day 1 -> Day 7，把 Week 1 的代码串起来读懂。**


## Week 1 总目标

这一周要解决的是：

1. Python 程序能读配置
2. Python 程序能请求大模型
3. 程序既能在 CLI 里聊天，也能作为 Web 服务启动
4. 流式输出、错误处理、测试这三件事不能缺

对应产物主要落在这些文件里：

- `app/config.py`
- `src/config/settings.py`
- `src/llm/client.py`
- `main.py`
- `tests/test_day1_config.py`
- `tests/test_settings.py`
- `tests/test_llm.py`
- `tests/test_main.py`
- `.env.example`


## Day 1：环境 + 函数 + 类型注解

这一天的代码重点看：

- [app/config.py](C:/Users/奶茶丸/Documents/agent/app/config.py)
- [tests/test_day1_config.py](C:/Users/奶茶丸/Documents/agent/tests/test_day1_config.py)
- [.env.example](C:/Users/奶茶丸/Documents/agent/.env.example)

你要关注的不是“功能强不强”，而是“项目有没有基本地基”。

在 `app/config.py` 里：

- `PROJECT_ROOT`：展示怎么用 `pathlib` 管路径
- `ENV_FILE`：展示项目默认 `.env` 在哪里
- `load_environment()`：展示怎么把 `.env` 读进来
- `get_env_value()`：展示 `str | None` 的实际用法
- `greet()`：最简单的类型注解练习

这一日的作用是：  
后面所有“不要把配置写死”的习惯，都是从这里开始的。


## Day 2：HTTP 请求

这一天的代码重点看：

- [src/llm/client.py](C:/Users/奶茶丸/Documents/agent/src/llm/client.py)

虽然你计划里写的是先用 `httpx` 请求公开 API，但在现在这个工程里，这层已经被收进 `LLMClient` 了。

你读这一天时，重点看这些概念：

- `LLMClient`：负责和外部模型 API 说话
- `_build_messages()`：把我们的输入整理成模型能理解的结构
- `chat()`：发一次完整请求
- `chat_stream()`：发一次流式请求

这一天的本质不是“学某个库”，而是理解：  
**你本地的 Python 程序，怎么把消息发给远端服务。**


## Day 3：LLM API 调通（支持动态 API Key）

这一天要连着看：

- [src/config/settings.py](C:/Users/奶茶丸/Documents/agent/src/config/settings.py)
- [.env.example](C:/Users/奶茶丸/Documents/agent/.env.example)
- [src/llm/client.py](C:/Users/奶茶丸/Documents/agent/src/llm/client.py)
- [main.py](C:/Users/奶茶丸/Documents/agent/main.py)
- [tests/test_llm.py](C:/Users/奶茶丸/Documents/agent/tests/test_llm.py)

要点有三个。

第一，默认配置从哪里来。  
在 `settings.py` 里，`llm_api_key`、`llm_base_url`、`llm_model` 这些默认值都从 `.env` 体系进入程序。

第二，为什么要支持动态 API Key。  
在 `src/llm/client.py` 的 `chat()`、`chat_stream()`、`list_models()` 里，你会看到：

- 不传运行时参数，就用默认配置
- 传了 `api_key/base_url/model`，就临时覆盖

这就是“用户自带 Key”的根本实现。

第三，Web 层怎么把请求头一路传进去。  
在 `main.py` 里，你会看到：

- `/v1/models`
- `/chat`
- `/chat/stream`

这几个接口都会从请求头读：

- `X-API-Key`
- `X-Base-URL`
- `X-Model`

然后传给 `LLMClient`。

这一日的作用是：  
**项目第一次真正拥有“能调模型”的心脏。**


## Day 4：SSE 流式输出

这一天重点看：

- [src/llm/client.py](C:/Users/奶茶丸/Documents/agent/src/llm/client.py)
- [main.py](C:/Users/奶茶丸/Documents/agent/main.py)
- [tests/test_main.py](C:/Users/奶茶丸/Documents/agent/tests/test_main.py)

要按两层来理解。

第一层：模型本身会不会流。  
在 `LLMClient.chat_stream()` 里，模型是逐块返回内容的。

第二层：后端会不会把这个流继续传给前端。  
在 `main.py` 的 `/chat/stream` 里，后端把每个 token 包成：

```text
data: {"content": "..."}

```

最后再发一条：

```text
data: [DONE]

```

这就是 SSE 的最小协议。

这一日的作用是：  
**把“模型内部在流”变成“用户眼里在流”。**


## Day 5：CLI 工具 + FastAPI 骨架

这一天重点看：

- [main.py](C:/Users/奶茶丸/Documents/agent/main.py)
- [tests/test_main.py](C:/Users/奶茶丸/Documents/agent/tests/test_main.py)
- [src/config/settings.py](C:/Users/奶茶丸/Documents/agent/src/config/settings.py)

这里有两个入口。

第一个入口是 `CLIChat`。  
这是给开发者调试用的最小对话工具。你可以直接在终端输入、马上看到回答。

第二个入口是 `create_app()`。  
这让程序不只是一个脚本，而是一个 Web 服务。

你要重点读：

- `CLIChat.run()`：CLI 交互循环
- `/health`：健康检查
- `/chat`：非流式接口
- `/chat/stream`：流式接口
- `main()`：决定启动 CLI 还是启动 Web

这一日的作用是：  
**把“模型调用代码”变成“应用骨架”。**


## Day 6：错误处理 + 重试

这一天重点看：

- [src/llm/client.py](C:/Users/奶茶丸/Documents/agent/src/llm/client.py)
- [tests/test_llm.py](C:/Users/奶茶丸/Documents/agent/tests/test_llm.py)

关键函数是：

- `_with_retries()`

这个函数解决的问题是：

- API 限流怎么办
- API 临时失败怎么办
- 网络波动怎么办

你要重点理解这几个点：

- `max_retries`
- `retry_backoff`
- `RateLimitError`
- `APIError`
- `OSError`

简单说，它不是让程序“永远不会错”，而是让程序别因为一次短暂抖动就立刻失败。

这一日的作用是：  
**把 Demo 变成更像产品的东西。**


## Day 7：测试 + Git

这一天重点看：

- [tests/test_day1_config.py](C:/Users/奶茶丸/Documents/agent/tests/test_day1_config.py)
- [tests/test_settings.py](C:/Users/奶茶丸/Documents/agent/tests/test_settings.py)
- [tests/test_llm.py](C:/Users/奶茶丸/Documents/agent/tests/test_llm.py)
- [tests/test_main.py](C:/Users/奶茶丸/Documents/agent/tests/test_main.py)
- [docs/git-cheatsheet.md](C:/Users/奶茶丸/Documents/agent/docs/git-cheatsheet.md)

这一日的重点不是“写更多功能”，而是确认这周做出来的东西能被验证、能被回滚、能继续迭代。

测试层面：

- `test_day1_config.py`：测 Day 1 的配置地基
- `test_settings.py`：测正式配置层
- `test_llm.py`：测 LLM 调用、流式、重试、动态参数
- `test_main.py`：测 Web 骨架、SSE、主入口接口

Git 层面：

- 你要学会先 `git status`
- 再 `git diff`
- 不确定就先 `git stash`
- 只回滚单文件就 `git restore`

这一日的作用是：  
**保证你下周继续改的时候，不会因为害怕弄坏现在的成果而不敢动。**


## 把 Week 1 串成一句话

如果你把这周当成一条链，它其实是：

1. Day 1：先把配置和环境打牢
2. Day 2：学会请求外部服务
3. Day 3：把请求真正接到大模型
4. Day 4：让返回结果流起来
5. Day 5：给它做 CLI 和 Web 两个入口
6. Day 6：让它出错时别一下子碎掉
7. Day 7：用测试和 Git 把这一周封箱


## 你读代码的推荐顺序

如果你现在准备真的去读一遍代码，我建议按这个顺序：

1. `app/config.py`
2. `src/config/settings.py`
3. `.env.example`
4. `src/llm/client.py`
5. `main.py`
6. `tests/test_day1_config.py`
7. `tests/test_settings.py`
8. `tests/test_llm.py`
9. `tests/test_main.py`
10. `docs/git-cheatsheet.md`

这样读的好处是：  
你会先理解“配置怎么进来”，再理解“消息怎么出去”，最后理解“怎么证明它没坏”。


## 最后一句

如果你能把 Week 1 看懂，你后面做 RAG 和 Agent 时，脑子里就不会只有“AI 很神奇”，而会变成：

**我知道配置从哪来、请求从哪发、流式怎么走、报错怎么兜、测试怎么保底。**

这就是 Week 1 真正的价值。
