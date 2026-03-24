import time
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.conversion.request_converter import convert_claude_to_openai
from src.conversion.response_converter import (
    convert_openai_streaming_to_claude_with_cancellation,
    convert_openai_to_claude_response,
)
from src.core.client import client_registry
from src.core.config import config
from src.core.logging import emit_request_audit_log, logger
from src.core.model_manager import model_manager
from src.models.claude import ClaudeMessagesRequest, ClaudeTokenCountRequest

router = APIRouter()


async def validate_api_key(
    x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)
):
    client_api_key = None
    if x_api_key:
        client_api_key = x_api_key
    elif authorization and authorization.startswith("Bearer "):
        client_api_key = authorization.replace("Bearer ", "")

    client_context = config.validate_client_api_key(client_api_key)
    if client_context is None:
        logger.warning("Invalid API key provided by client")
        raise HTTPException(
            status_code=401,
            detail="Invalid API key. Please provide a valid client API key.",
        )
    return client_context


@router.post("/v1/messages")
async def create_message(
    request: ClaudeMessagesRequest,
    http_request: Request,
    client_context: dict = Depends(validate_api_key),
):
    request_id = str(uuid.uuid4())
    started_at = time.perf_counter()
    route_result = None
    model_name = request.model.strip()
    try:
        route_result = model_manager.resolve_route(request.model)
    except Exception as e:
        logger.error(f"Unexpected error processing request: {e}")
        model_list_names = []
        [model_list_names.extend([item["model_map"]["big"], item["model_map"]["middle"],item["model_map"]["small"]]) for item in model_manager.config.providers]
        raise HTTPException(status_code=500, detail=f"you set model ‘{model_name}’ not support ！！！ allow use models: {set(model_list_names)} ")
    openai_client = client_registry.get_client(route_result["provider_name"])
    openai_request = None
    if route_result.get("platform") == "tx":
        from src.conversion.tx.tx_request_converter import convert_claude_to_openai as c2v
        openai_request = c2v(request, route_result["target_model"])
    else:
        openai_request = convert_claude_to_openai(request, route_result["target_model"])
    audit_input = build_audit_input(request)
    try:
        if await http_request.is_disconnected():
            raise HTTPException(status_code=499, detail="Client disconnected")

        if request.stream:
            audit_context = {}
            openai_stream = openai_client.create_chat_completion_stream(
                openai_request, request_id
            )

            async def streaming_wrapper():
                try:
                    async for chunk in convert_openai_streaming_to_claude_with_cancellation(
                        openai_stream,
                        request,
                        logger,
                        http_request,
                        openai_client,
                        request_id,
                        audit_context,
                    ):
                        yield chunk
                finally:
                    duration_ms = int((time.perf_counter() - started_at) * 1000)
                    emit_request_audit_log(
                        "request_completed",
                        build_audit_payload(
                            request_id=request_id,
                            client_context=client_context,
                            route_result=route_result,
                            stream=True,
                            status="cancelled"
                            if audit_context.get("cancelled")
                            else ("error" if audit_context.get("error") else "success"),
                            duration_ms=duration_ms,
                            audit_input=audit_input,
                            audit_output=build_stream_audit_output(audit_context),
                            usage=audit_context.get("usage", {}),
                            error=audit_context.get("error"),
                        ),
                    )

            return StreamingResponse(
                streaming_wrapper(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Headers": "*",
                },
            )

        openai_response = await openai_client.create_chat_completion(openai_request, request_id)
        claude_response = convert_openai_to_claude_response(openai_response, request)
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        emit_request_audit_log(
            "request_completed",
            build_audit_payload(
                request_id=request_id,
                client_context=client_context,
                route_result=route_result,
                stream=False,
                status="success",
                duration_ms=duration_ms,
                audit_input=audit_input,
                audit_output=claude_response,
                usage=claude_response.get("usage", {}),
                error=None,
            ),
        )
        return claude_response
    except HTTPException as exc:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        emit_request_audit_log(
            "request_completed",
            build_audit_payload(
                request_id=request_id,
                client_context=client_context,
                route_result=route_result,
                stream=bool(request.stream),
                status="cancelled" if exc.status_code == 499 else "error",
                duration_ms=duration_ms,
                audit_input=audit_input,
                audit_output=None,
                usage={},
                error=exc.detail,
            ),
        )
        raise
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        logger.error(f"Unexpected error processing request: {exc}")
        error_message = openai_client.classify_openai_error(str(exc))
        emit_request_audit_log(
            "request_completed",
            build_audit_payload(
                request_id=request_id,
                client_context=client_context,
                route_result=route_result,
                stream=bool(request.stream),
                status="error",
                duration_ms=duration_ms,
                audit_input=audit_input,
                audit_output=None,
                usage={},
                error=error_message,
            ),
        )
        raise HTTPException(status_code=500, detail=error_message)


@router.post("/v1/messages/count_tokens")
async def count_tokens(
    request: ClaudeTokenCountRequest, _: dict = Depends(validate_api_key)
):
    try:
        total_chars = 0
        if request.system:
            if isinstance(request.system, str):
                total_chars += len(request.system)
            elif isinstance(request.system, list):
                for block in request.system:
                    if hasattr(block, "text"):
                        total_chars += len(block.text)

        for msg in request.messages:
            if msg.content is None:
                continue
            if isinstance(msg.content, str):
                total_chars += len(msg.content)
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if hasattr(block, "text") and block.text is not None:
                        total_chars += len(block.text)

        return {"input_tokens": max(1, total_chars // 4)}
    except Exception as e:
        logger.error(f"Error counting tokens: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "providers": len(config.providers),
        "provider_overview": config.get_providers_summary(),
        "default_provider": config.get_default_provider_name(),
        "client_api_key_validation": config.is_client_auth_enabled(),
    }


@router.get("/test-connection")
async def test_connection():
    provider = config.get_default_provider()
    if not provider:
        return JSONResponse(
            status_code=503,
            content={
                "status": "failed",
                "message": "No default provider configured",
                "timestamp": datetime.now().isoformat(),
            },
        )

    client = client_registry.get_client(provider["name"])
    model = provider["model_map"].get("small") or provider["model_map"].get("big")

    try:
        test_response = await client.create_chat_completion(
            {
                "model": model,
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 5,
            }
        )
        return {
            "status": "success",
            "message": "Successfully connected to upstream provider",
            "provider": {
                "name": provider["name"],
                "platform": provider["platform"],
                "base_url": provider["base_url"],
            },
            "model_used": model,
            "timestamp": datetime.now().isoformat(),
            "response_id": test_response.get("id", "unknown"),
        }
    except Exception as e:
        logger.error(f"API connectivity test failed: {e}")
        return JSONResponse(
            status_code=503,
            content={
                "status": "failed",
                "error_type": "API Error",
                "message": str(e),
                "provider": {
                    "name": provider["name"],
                    "platform": provider["platform"],
                    "base_url": provider["base_url"],
                },
                "timestamp": datetime.now().isoformat(),
            },
        )


@router.get("/")
async def root():
    return {
        "message": "Claude-to-OpenAI API Proxy v1.0.0",
        "status": "running",
        "config": {
            "providers": len(config.providers),
            "provider_overview": config.get_providers_summary(),
            "default_provider": config.get_default_provider_name(),
            "max_tokens_limit": config.max_tokens_limit,
            "client_api_key_validation": config.is_client_auth_enabled(),
        },
        "endpoints": {
            "messages": "/v1/messages",
            "count_tokens": "/v1/messages/count_tokens",
            "health": "/health",
            "test_connection": "/test-connection",
        },
    }


def build_audit_payload(
    request_id: str,
    client_context: dict,
    route_result: dict,
    stream: bool,
    status: str,
    duration_ms: int,
    audit_input,
    audit_output,
    usage: dict,
    error,
):
    return {
        "request_id": request_id,
        "client_token_masked": client_context.get("client_token_masked"),
        "auth_mode": client_context.get("auth_mode"),
        "provider_name": route_result.get("provider_name"),
        "provider_platform": route_result.get("platform"),
        "provider_base_url": route_result.get("base_url"),
        "requested_model": route_result.get("requested_model"),
        "target_model": route_result.get("target_model"),
        "stream": stream,
        "status": status,
        "duration_ms": duration_ms,
        "input": audit_input,
        "output": audit_output,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "error": error,
    }


def build_audit_input(request: ClaudeMessagesRequest):
    payload = request.model_dump()
    return sanitize_for_logging(payload)


def build_stream_audit_output(audit_context: dict):
    return {
        "text": audit_context.get("output_text", ""),
        "tool_calls": audit_context.get("tool_calls", []),
        "stop_reason": audit_context.get("stop_reason"),
        "cancelled": audit_context.get("cancelled", False),
    }


def sanitize_for_logging(value):
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            if key in {"api_key", "authorization", "custom_headers"}:
                continue
            if key == "data" and isinstance(item, str):
                max_chars = config.get_logging_config()["max_image_log_chars"]
                sanitized[key] = item[:max_chars] + ("..." if len(item) > max_chars else "")
            else:
                sanitized[key] = sanitize_for_logging(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_for_logging(item) for item in value]
    return value
