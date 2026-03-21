# Claude Code Proxy

A FastAPI proxy that exposes Anthropic-compatible `/v1/messages` endpoints and forwards requests to one or more OpenAI-compatible upstream providers.

It is designed for Claude Code and other Anthropic-compatible clients while preserving the existing Claude ↔ OpenAI conversion behavior, tool use conversion, image input support, streaming SSE responses, and request cancellation.

## Features

- Anthropic-compatible `/v1/messages` and `/v1/messages/count_tokens`
- Route requests to multiple OpenAI-compatible providers
- Route by exact model matches from provider `model_map` values
- Support multiple client access tokens
- Structured request audit logging by client token
- Streaming support with upstream cancellation on disconnect
- Tool use / tool result conversion
- Image input support
- Legacy single-provider environment variable mode still supported

## Quick start

### 1. Install

```bash
uv sync
```

Or:

```bash
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Then edit `.env`.

You can run the proxy in either of these modes:

1. **Legacy mode**: one upstream provider configured with environment variables.
2. **JSON mode**: multiple providers, multiple client tokens, and logging options configured through `PROXY_CONFIG_JSON`.

### 3. Start

```bash
python start_proxy.py
```

Or:

```bash
uv run claude-code-proxy
```

Or:

```bash
docker compose up -d
```

### 4. Point Claude Code at the proxy

Anonymous access enabled in the proxy:

```bash
ANTHROPIC_BASE_URL=http://localhost:8082 ANTHROPIC_API_KEY=dummy claude
```

Token validation enabled in the proxy:

```bash
ANTHROPIC_BASE_URL=http://localhost:8082 ANTHROPIC_API_KEY=client-token-a claude
```

## How requests flow

1. The proxy receives an Anthropic-compatible request at `/v1/messages`.
2. It validates the client token from `x-api-key` or `Authorization: Bearer ...`.
3. It resolves the requested model to a provider and target model.
4. It converts the Claude request to OpenAI Chat Completions format.
5. It sends the request through the selected upstream client.
6. It converts the upstream response back to Claude format.
7. It writes a structured audit log with caller, route, models, usage, input, and output.

## Configuration overview

The proxy automatically loads `.env` through `python-dotenv` when started via the normal entrypoint.

### Mode A: legacy environment-variable mode

This mode keeps existing deployments working.

Required:

- `OPENAI_API_KEY`

Optional:

- `OPENAI_BASE_URL` default: `https://api.openai.com/v1`
- `AZURE_API_VERSION` enables Azure OpenAI client behavior
- `BIG_MODEL` default: `gpt-4o`
- `MIDDLE_MODEL` default: `BIG_MODEL`
- `SMALL_MODEL` default: `gpt-4o-mini`
- `ANTHROPIC_API_KEY` single client token; if omitted, anonymous access is allowed
- `CUSTOM_HEADER_*` custom upstream headers
- `HOST` default: `0.0.0.0`
- `PORT` default: `8082`
- `LOG_LEVEL` default: `INFO`
- `MAX_TOKENS_LIMIT` default: `4096`
- `MIN_TOKENS_LIMIT` default: `100`
- `REQUEST_TIMEOUT` default: `90`
- `MAX_RETRIES` default: `2`

Legacy model mapping:

| Requested Claude model contains | Target model |
| --- | --- |
| `haiku` | `SMALL_MODEL` |
| `sonnet` | `MIDDLE_MODEL` |
| `opus` | `BIG_MODEL` |
| anything else | request fails |

### Mode B: `PROXY_CONFIG_JSON`

Use `PROXY_CONFIG_JSON` when you need multiple upstreams, multiple client tokens, or logging controls.

Top-level structure:

- `providers`
- `client_tokens`
- `logging`

Example:

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

Example in `.env`:

```bash
PROXY_CONFIG_JSON='{"providers":[{"name":"openai-main","platform":"openai","base_url":"https://api.openai.com/v1","api_key":"sk-xxx","api_version":null,"model_map":{"big":"gpt-4o","middle":"gpt-4o","small":"gpt-4o-mini"},"custom_headers":{}}],"client_tokens":["client-token-a"],"logging":{"log_full_input_output":true,"max_image_log_chars":2000,"mask_token_in_logs":true}}'
```

## Provider configuration

Each provider entry supports:

- `name`: unique provider name
- `platform`: descriptive label such as `openai`, `azure`, `deepseek`, `ollama`
- `base_url`: upstream OpenAI-compatible base URL
- `api_key`: upstream key
- `api_version`: for Azure-style clients
- `model_map.big|middle|small`: target model names used for direct match and Claude alias fallback
- `custom_headers`: extra upstream headers for this provider only

### Azure note

If you use Azure OpenAI, treat each deployment you want to route to as its own provider entry. This keeps routing simple and matches the current client behavior.

## Routing rules

The router resolves requests in this order:

1. Exact match against every configured `providers[].model_map` value
2. If not matched, Claude-style aliases on the default provider:
   - `haiku` → `small`
   - `sonnet` → `middle`
   - `opus` → `big`
3. If none of the above match, the request fails

Important rules:

- The first provider in `providers` is the default provider
- `providers[].model_map` values must be unique across providers
- `model_routing` is no longer supported
- Direct matches keep the requested model as `target_model`
- Alias routing uses the default provider’s `model_map`
- If an alias slot is empty, routing falls back to the default provider’s `big` model

Examples:

- Request `gpt-4o-mini` and route to the provider whose `model_map` contains `gpt-4o-mini`
- Request `deepseek-chat` and route to the provider whose `model_map` contains `deepseek-chat`
- Request `claude-3-5-sonnet-20241022` and route to the default provider’s `middle` model

## Client authentication

The proxy reads client credentials from either:

- `x-api-key`
- `Authorization: Bearer ...`

Behavior:

- If `client_tokens` is configured, only those tokens are accepted.
- If `client_tokens` is not configured but legacy `ANTHROPIC_API_KEY` is set, that single token is accepted.
- If neither is configured, anonymous access is allowed.

`client_tokens` must be configured as a JSON string array, for example:

```json
["client-token-a", "client-token-b"]
```

Legacy `ANTHROPIC_API_KEY` still works as a single-token fallback when `client_tokens` is not configured. If both are present, `client_tokens` takes precedence.

## Logging and audit data

The proxy writes structured request audit logs including:

- request ID
- masked client token
- auth mode
- provider name
- provider platform
- provider base URL
- requested model
- target model
- streaming flag
- status: success / error / cancelled
- duration
- input
- output
- input/output tokens
- error message

Default behavior in JSON config mode is to log full input and output content.

Important logging rules:

- API keys are not written to logs
- custom headers are not written to logs
- image base64 payloads are truncated using `max_image_log_chars`

## Endpoint reference

### `POST /v1/messages`

Anthropic-compatible message endpoint.

Example:

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

Streaming example:

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

Returns a rough token estimate based on character count.

### `GET /health`

Returns safe operational summary only:

- provider count
- provider overview
- default provider
- whether client token validation is enabled

### `GET /test-connection`

Runs a small test call against the default provider.

### `GET /`

Returns a basic service summary and endpoint list.

## Using with Claude Code

Point Claude Code to the proxy base URL.

Anonymous mode:

```bash
ANTHROPIC_BASE_URL=http://localhost:8082 ANTHROPIC_API_KEY=dummy claude
```

Token-protected mode:

```bash
ANTHROPIC_BASE_URL=http://localhost:8082 ANTHROPIC_API_KEY=client-token-a claude
```

If your Claude Code workflow sends `Authorization: Bearer ...` instead of `x-api-key`, that is also accepted by the proxy.

## Provider examples

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

### Ollama or other local OpenAI-compatible service

```bash
OPENAI_API_KEY="dummy-key"
OPENAI_BASE_URL="http://localhost:11434/v1"
BIG_MODEL="llama3.1:70b"
MIDDLE_MODEL="llama3.1:70b"
SMALL_MODEL="llama3.1:8b"
```

## Custom headers in legacy mode

You can add upstream headers with `CUSTOM_HEADER_*` environment variables.

Example:

```bash
CUSTOM_HEADER_AUTHORIZATION="Bearer upstream-token"
CUSTOM_HEADER_X_API_KEY="provider-key"
CUSTOM_HEADER_USER_AGENT="my-app/1.0.0"
```

Environment variable names are converted by removing `CUSTOM_HEADER_` and replacing underscores with hyphens.

## Verification checklist

- Start with `PROXY_CONFIG_JSON` using at least 2 providers and 2 client tokens
- Confirm startup fails if two providers reuse the same `model_map` value
- Confirm startup fails if `model_routing` is still present
- Start again with legacy env vars only and confirm compatibility
- Test valid and invalid `x-api-key`
- Test `Authorization: Bearer ...`
- Test anonymous access when no client token config is set
- Test exact matching against configured provider model names
- Test `haiku`, `sonnet`, and `opus` alias routing
- Test an unknown model and confirm it fails
- Test non-streaming response conversion
- Test streaming response conversion
- Disconnect a streaming client and confirm cancellation is reflected in logs
- Check `/health`, `/test-connection`, and `/`
- Confirm API keys and custom headers do not appear in logs

## Development

```bash
uv run black src tests
uv run isort src tests
uv run mypy src
pytest
```

## Chinese documentation

See `README.zh-CN.md`.

## License

MIT License
