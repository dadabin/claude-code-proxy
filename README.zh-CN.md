# Claude Code Proxy 中文文档

这是一个 FastAPI 代理服务，对外暴露 Anthropic 兼容的 `/v1/messages` 接口，并把请求转发到一个或多个 OpenAI-compatible 上游平台。

它适合接入 Claude Code 或其他兼容 Anthropic 协议的客户端，同时保留现有的 Claude ↔ OpenAI 协议转换、工具调用转换、图片输入、流式 SSE 返回，以及断连取消上游请求的行为。

## 功能特性

- 兼容 Anthropic `/v1/messages` 与 `/v1/messages/count_tokens`
- 支持多个 OpenAI-compatible 上游平台
- 基于 provider `model_map` 的精确模型名路由
- 支持多个客户端访问 token
- 按 token 维度输出结构化调用审计日志
- 支持流式响应和客户端断连取消
- 支持 tool use / tool result 转换
- 支持图片输入
- 兼容旧版单 provider 环境变量配置方式

## 快速开始

### 1. 安装依赖

```bash
uv sync
```

或者：

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

然后编辑 `.env`。

当前支持两种配置方式：

1. **旧版模式**：使用环境变量配置单一上游平台。
2. **JSON 模式**：通过 `PROXY_CONFIG_JSON` 配置多 provider、多 client token 和日志选项。

### 3. 启动服务

```bash
python start_proxy.py
```

或者：

```bash
uv run claude-code-proxy
```

或者：

```bash
docker compose up -d
```

### 4. 让 Claude Code 连接到代理

如果代理未开启 token 校验：

```bash
ANTHROPIC_BASE_URL=http://localhost:8082 ANTHROPIC_API_KEY=dummy claude
```

如果代理开启了 client token 校验：

```bash
ANTHROPIC_BASE_URL=http://localhost:8082 ANTHROPIC_API_KEY=client-token-a claude
```

## 请求处理流程

1. 客户端向 `/v1/messages` 发送 Anthropic 格式请求。
2. 代理从 `x-api-key` 或 `Authorization: Bearer ...` 中读取客户端 token 并校验。
3. 根据请求模型名解析出 provider 和目标模型。
4. 把 Claude 请求转换为 OpenAI Chat Completions 请求。
5. 通过选中的上游 client 发起请求。
6. 把上游响应转换回 Claude 格式。
7. 输出结构化调用日志，记录调用者、路由结果、模型、usage、input、output 等信息。

## 配置说明

代理通过正常入口启动时，会自动从 `.env` 加载环境变量。

## 方式一：旧版环境变量模式

这个模式用于兼容原有部署方式。

必填：

- `OPENAI_API_KEY`

可选：

- `OPENAI_BASE_URL`，默认 `https://api.openai.com/v1`
- `AZURE_API_VERSION`，设置后启用 Azure OpenAI 客户端逻辑
- `BIG_MODEL`，默认 `gpt-4o`
- `MIDDLE_MODEL`，默认 `BIG_MODEL`
- `SMALL_MODEL`，默认 `gpt-4o-mini`
- `ANTHROPIC_API_KEY`，旧版单 token 校验；不设置则允许匿名访问
- `CUSTOM_HEADER_*`，上游自定义请求头
- `HOST`，默认 `0.0.0.0`
- `PORT`，默认 `8082`
- `LOG_LEVEL`，默认 `INFO`
- `MAX_TOKENS_LIMIT`，默认 `4096`
- `MIN_TOKENS_LIMIT`，默认 `100`
- `REQUEST_TIMEOUT`，默认 `90`
- `MAX_RETRIES`，默认 `2`

旧版模型映射规则：

| 请求模型名包含 | 实际目标模型 |
| --- | --- |
| `haiku` | `SMALL_MODEL` |
| `sonnet` | `MIDDLE_MODEL` |
| `opus` | `BIG_MODEL` |
| 其他 | 请求报错 |

## 方式二：`PROXY_CONFIG_JSON`

如果你需要多上游、多客户端 token 或更细的日志配置，推荐使用 `PROXY_CONFIG_JSON`。

顶层包含这些区块：

- `providers`
- `client_tokens`
- `logging`

示例：

```json
{
  "providers": [
    {
      "name": "openai-main",
      "platform": "openai",
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-xxx",
      "api_version": null,
      "model_map": {
        "big": "gpt-4o",
        "middle": "gpt-4o",
        "small": "gpt-4o-mini"
      },
      "custom_headers": {}
    },
    {
      "name": "deepseek-main",
      "platform": "deepseek",
      "base_url": "https://api.deepseek.com/v1",
      "api_key": "sk-yyy",
      "api_version": null,
      "model_map": {
        "big": "deepseek-reasoner",
        "middle": "deepseek-chat",
        "small": "deepseek-chat-lite"
      },
      "custom_headers": {}
    }
  ],
  "client_tokens": [
    "client-token-a",
    "client-token-b"
  ],
  "logging": {
    "log_full_input_output": true,
    "max_image_log_chars": 2000,
    "mask_token_in_logs": true
  }
}
```

`.env` 中的示例写法：

```bash
PROXY_CONFIG_JSON='{"providers":[{"name":"openai-main","platform":"openai","base_url":"https://api.openai.com/v1","api_key":"sk-xxx","api_version":null,"model_map":{"big":"gpt-4o","middle":"gpt-4o","small":"gpt-4o-mini"},"custom_headers":{}}],"client_tokens":["client-token-a"],"logging":{"log_full_input_output":true,"max_image_log_chars":2000,"mask_token_in_logs":true}}'
```

## Provider 配置说明

每个 provider 支持这些字段：

- `name`：唯一名称
- `platform`：平台标识，例如 `openai`、`azure`、`deepseek`、`ollama`
- `base_url`：上游 OpenAI-compatible API 地址
- `api_key`：上游平台密钥
- `api_version`：Azure 场景使用
- `model_map.big|middle|small`：用于精确匹配和 Claude 别名回退的目标模型名
- `custom_headers`：仅对该 provider 生效的自定义请求头

### Azure 使用建议

如果你要路由到多个 Azure deployment，建议把每个 deployment 配成一个独立 provider，而不是在单一 provider 里做复杂分支。

## 模型路由规则

路由优先级如下：

1. 先对所有 `providers[].model_map` 的模型值做精确匹配
2. 未命中时，再使用默认 provider 的 Claude 风格别名：
   - `haiku` → `small`
   - `sonnet` → `middle`
   - `opus` → `big`
3. 如果以上都不命中，则请求直接失败

重要规则：

- `providers` 中的第一个 provider 就是默认 provider
- 所有 `providers[].model_map` 中的模型值必须全局唯一
- `model_routing` 已不再支持
- 精确命中时，`target_model` 就是请求里的原始模型名
- Claude 风格别名路由使用默认 provider 的 `model_map`
- 如果别名对应槽位为空，则回退到默认 provider 的 `big` 模型

常见用法：

- 请求 `gpt-4o-mini`，路由到 `model_map` 中包含该值的 provider
- 请求 `deepseek-chat`，路由到 `model_map` 中包含该值的 provider
- 请求 `claude-3-5-sonnet-20241022`，路由到默认 provider 的 `middle` 模型

## 客户端鉴权

代理会从以下头部读取客户端凭证：

- `x-api-key`
- `Authorization: Bearer ...`

行为如下：

- 如果配置了 `client_tokens`，则只允许这些 token 访问
- 如果未配置 `client_tokens`，但设置了旧版 `ANTHROPIC_API_KEY`，则接受这个单 token
- 如果两者都未配置，则允许匿名访问

`client_tokens` 必须配置为 JSON 字符串数组，例如：

```json
["client-token-a", "client-token-b"]
```

当未配置 `client_tokens` 时，旧版 `ANTHROPIC_API_KEY` 仍可作为单 token 兜底继续生效；如果两者同时存在，则优先使用 `client_tokens`。

## 日志与审计信息

代理会输出结构化调用日志，包含：

- request ID
- 脱敏后的 client token
- auth mode
- provider name
- provider platform
- provider base URL
- requested model
- target model
- 是否流式
- 状态：success / error / cancelled
- duration
- input
- output
- input/output token
- error

当前 JSON 配置模式下，默认会记录完整明文 input/output。

日志边界：

- API key 不进入日志
- custom headers 不进入日志
- 图片 base64 会按 `max_image_log_chars` 截断

## 接口说明

### `POST /v1/messages`

Anthropic 兼容消息接口。

示例：

```bash
curl http://localhost:8082/v1/messages \
  -H 'content-type: application/json' \
  -H 'x-api-key: client-token-a' \
  -d '{
    "model": "claude-3-5-sonnet-20241022",
    "max_tokens": 128,
    "messages": [
      {"role": "user", "content": "Say hello in one sentence."}
    ]
  }'
```

流式示例：

```bash
curl http://localhost:8082/v1/messages \
  -H 'content-type: application/json' \
  -H 'x-api-key: client-token-a' \
  -d '{
    "model": "claude-3-5-sonnet-20241022",
    "max_tokens": 128,
    "stream": true,
    "messages": [
      {"role": "user", "content": "Write three short lines."}
    ]
  }'
```

### `POST /v1/messages/count_tokens`

按字符数粗略估算 token 数。

### `GET /health`

返回安全的运行概览：

- provider 数量
- provider 摘要
- 默认 provider
- 是否启用了客户端 token 校验

### `GET /test-connection`

对默认 provider 发起一个小请求，用于检查连通性。

### `GET /`

返回服务摘要和接口列表。

## 与 Claude Code 集成

把 Claude Code 的 base URL 指向本代理。

匿名模式：

```bash
ANTHROPIC_BASE_URL=http://localhost:8082 ANTHROPIC_API_KEY=dummy claude
```

开启 token 校验模式：

```bash
ANTHROPIC_BASE_URL=http://localhost:8082 ANTHROPIC_API_KEY=client-token-a claude
```

如果你的客户端发送的是 `Authorization: Bearer ...`，代理也支持。

## Provider 示例

### OpenAI

```bash
OPENAI_API_KEY="sk-your-openai-key"
OPENAI_BASE_URL="https://api.openai.com/v1"
BIG_MODEL="gpt-4o"
MIDDLE_MODEL="gpt-4o"
SMALL_MODEL="gpt-4o-mini"
```

### Azure OpenAI

```bash
OPENAI_API_KEY="your-azure-key"
OPENAI_BASE_URL="https://your-resource.openai.azure.com/openai/deployments/your-deployment"
AZURE_API_VERSION="2024-03-01-preview"
BIG_MODEL="gpt-4"
MIDDLE_MODEL="gpt-4"
SMALL_MODEL="gpt-35-turbo"
```

### Ollama 或其他本地 OpenAI-compatible 服务

```bash
OPENAI_API_KEY="dummy-key"
OPENAI_BASE_URL="http://localhost:11434/v1"
BIG_MODEL="llama3.1:70b"
MIDDLE_MODEL="llama3.1:70b"
SMALL_MODEL="llama3.1:8b"
```

## 旧版模式下的自定义请求头

你可以用 `CUSTOM_HEADER_*` 环境变量向上游附加请求头。

例如：

```bash
CUSTOM_HEADER_AUTHORIZATION="Bearer upstream-token"
CUSTOM_HEADER_X_API_KEY="provider-key"
CUSTOM_HEADER_USER_AGENT="my-app/1.0.0"
```

转换规则是：去掉 `CUSTOM_HEADER_` 前缀，再把下划线替换成中划线。

## 验证清单

- 用 `PROXY_CONFIG_JSON` 启动，至少配置 2 个 provider 和 2 个 client token
- 确认两个 provider 复用同一个 `model_map` 值时启动失败
- 确认配置中仍包含 `model_routing` 时启动失败
- 再只用旧版环境变量启动一次，确认兼容性
- 测试正确和错误的 `x-api-key`
- 测试 `Authorization: Bearer ...`
- 在未配置 token 时测试匿名访问
- 测试对已配置 provider 模型名的精确匹配路由
- 测试 `haiku`、`sonnet`、`opus` 别名路由
- 测试未知模型并确认直接报错
- 测试非流式响应转换
- 测试流式响应转换
- 中途断开流式请求，确认取消逻辑生效并记录日志
- 检查 `/health`、`/test-connection` 和 `/`
- 确认 API key 和 custom headers 不进入日志

## 开发命令

```bash
uv run black src tests
uv run isort src tests
uv run mypy src
pytest
```

## 英文文档

英文版本见 `README.md`。

## License

MIT License
