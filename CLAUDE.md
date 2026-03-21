# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Setup
- `uv sync` — install runtime and dev dependencies into the uv-managed environment.
- `pip install -r requirements.txt` — alternative install path if uv is unavailable.
- `cp .env.example .env` — create local configuration before running the proxy.

### Run
- `python start_proxy.py` — start the proxy with `.env` auto-loading.
- `uv run claude-code-proxy` — run the packaged CLI entrypoint.
- `docker compose up -d` — run the service in Docker on port `8082`.

### Tests
- `pytest` — run the automated test suite.
- `pytest tests/test_main.py` — run the main pytest file only.
- `python test_cancellation.py` — run the manual cancellation test script against a running local server.
- `python src/test_claude_to_openai.py` — run the manual end-to-end proxy exercise script against a running local server, if that file exists in the current branch.

### Quality
- `uv run black src tests` — format Python code.
- `uv run isort src tests` — sort imports.
- `uv run mypy src` — run static type checks.

## Configuration
- Runtime configuration is environment-driven and loaded from `.env`.
- `OPENAI_API_KEY` is required at import time because `src/core/config.py` constructs `config` eagerly.
- `ANTHROPIC_API_KEY` is optional; when set, incoming client requests must send the matching key via `x-api-key` or `Authorization: Bearer ...`.
- Legacy model alias routing is controlled by `BIG_MODEL`, `MIDDLE_MODEL`, and `SMALL_MODEL`; in JSON mode, provider selection comes from exact matches against `providers[].model_map` values, and the first provider is the default provider.
- `OPENAI_BASE_URL` can target OpenAI, Azure OpenAI, Ollama, or any OpenAI-compatible provider.
- `CUSTOM_HEADER_*` environment variables are turned into upstream HTTP headers by `Config.get_custom_headers()`.

## Architecture
- This is a FastAPI proxy that exposes Anthropic-compatible endpoints and translates them into OpenAI chat completions calls.
- Entry point: `src/main.py` creates the FastAPI app and mounts the API router from `src/api/endpoints.py`.
- Request flow for `/v1/messages`:
  1. `src/api/endpoints.py` validates the optional Anthropic client key.
  2. The Claude-format request is validated by Pydantic models in `src/models/claude.py`.
  3. `src/conversion/request_converter.py` maps Claude messages, tools, multimodal blocks, and tool results into OpenAI chat-completions payloads.
  4. `src/core/model_manager.py` first routes by exact matches against configured `providers[].model_map` values, then falls back to default-provider alias mapping for Claude model names containing `haiku` / `sonnet` / `opus`.
  5. `src/core/client.py` sends the request through `AsyncOpenAI` or `AsyncAzureOpenAI`, and also tracks active requests for cancellation.
  6. `src/conversion/response_converter.py` converts the upstream response or SSE stream back into Anthropic message/content-block format.
- Streaming is a first-class path: the proxy emits Anthropic-style SSE events (`message_start`, `content_block_delta`, `message_stop`) while consuming OpenAI streaming chunks.
- Cancellation support spans both the endpoint and client layers: `src/api/endpoints.py` checks `Request.is_disconnected()`, and `src/core/client.py` maintains per-request cancellation events so upstream work can be stopped when the client disconnects.
- `/v1/messages/count_tokens` is only a rough estimator based on character count; it does not call a tokenizer service.

## Important implementation details
- `src/core/config.py` exits the process if `OPENAI_API_KEY` is missing, so tests or scripts that import app modules need env configured first.
- `MIDDLE_MODEL` defaults to `BIG_MODEL`; sonnet and opus can intentionally share the same upstream model.
- The code targets the OpenAI Chat Completions API, not the newer Responses API.
- Azure support is selected by the presence of `AZURE_API_VERSION`, which switches the client class to `AsyncAzureOpenAI`.
- Custom headers are merged with default headers in `src/core/client.py`; provider-specific auth or routing can be injected entirely through env.
- The root endpoint, `/health`, and `/test-connection` are useful for smoke-testing local changes without using Claude Code directly.

## Repo notes
- The existing README is the main source of setup and provider examples; keep CLAUDE.md focused on developer-operational details and architecture.
- There are no `.cursor/rules`, `.cursorrules`, or `.github/copilot-instructions.md` files in this repository as of this update.
