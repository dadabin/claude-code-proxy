import asyncio
import json
from typing import Optional, AsyncGenerator, Dict, Any

from fastapi import HTTPException
from openai import AsyncAzureOpenAI, AsyncOpenAI
from openai._exceptions import APIError, AuthenticationError, BadRequestError, RateLimitError

from src.core.config import config


class OpenAIClient:
    """Async OpenAI client with cancellation support."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        default_model: str,
        timeout: int = 90,
        api_version: Optional[str] = None,
        custom_headers: Optional[Dict[str, str]] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.custom_headers = custom_headers or {}

        default_headers = {
            "Content-Type": "application/json",
            "User-Agent": "claude-proxy/1.0.0",
        }
        all_headers = {**default_headers, **self.custom_headers}

        if api_version:
            self.client = AsyncAzureOpenAI(
                azure_deployment=default_model,
                api_key=api_key,
                azure_endpoint=base_url,
                api_version=api_version,
                timeout=timeout,
                default_headers=all_headers,
            )
        else:
            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                default_headers=all_headers,
            )
        self.active_requests: Dict[str, asyncio.Event] = {}

    async def create_chat_completion(
        self, request: Dict[str, Any], request_id: Optional[str] = None
    ) -> Dict[str, Any]:
        if request_id:
            cancel_event = asyncio.Event()
            self.active_requests[request_id] = cancel_event

        try:
            completion_task = asyncio.create_task(
                self.client.chat.completions.create(**request)
            )

            if request_id:
                cancel_task = asyncio.create_task(cancel_event.wait())
                done, pending = await asyncio.wait(
                    [completion_task, cancel_task], return_when=asyncio.FIRST_COMPLETED
                )

                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                if cancel_task in done:
                    completion_task.cancel()
                    raise HTTPException(status_code=499, detail="Request cancelled by client")

                completion = await completion_task
            else:
                completion = await completion_task

            return completion.model_dump()

        except AuthenticationError as e:
            raise HTTPException(status_code=401, detail=self.classify_openai_error(str(e)))
        except RateLimitError as e:
            raise HTTPException(status_code=429, detail=self.classify_openai_error(str(e)))
        except BadRequestError as e:
            raise HTTPException(status_code=400, detail=self.classify_openai_error(str(e)))
        except APIError as e:
            status_code = getattr(e, "status_code", 500)
            raise HTTPException(status_code=status_code, detail=self.classify_openai_error(str(e)))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")
        finally:
            if request_id and request_id in self.active_requests:
                del self.active_requests[request_id]

    async def create_chat_completion_stream(
        self, request: Dict[str, Any], request_id: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        if request_id:
            cancel_event = asyncio.Event()
            self.active_requests[request_id] = cancel_event

        try:
            request["stream"] = True
            if "stream_options" not in request:
                request["stream_options"] = {}
            request["stream_options"]["include_usage"] = True

            streaming_completion = await self.client.chat.completions.create(**request)

            async for chunk in streaming_completion:
                if request_id and request_id in self.active_requests:
                    if self.active_requests[request_id].is_set():
                        raise HTTPException(status_code=499, detail="Request cancelled by client")

                chunk_dict = chunk.model_dump()
                chunk_json = json.dumps(chunk_dict, ensure_ascii=False)
                yield f"data: {chunk_json}"

            yield "data: [DONE]"

        except AuthenticationError as e:
            raise HTTPException(status_code=401, detail=self.classify_openai_error(str(e)))
        except RateLimitError as e:
            raise HTTPException(status_code=429, detail=self.classify_openai_error(str(e)))
        except BadRequestError as e:
            raise HTTPException(status_code=400, detail=self.classify_openai_error(str(e)))
        except APIError as e:
            status_code = getattr(e, "status_code", 500)
            raise HTTPException(status_code=status_code, detail=self.classify_openai_error(str(e)))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")
        finally:
            if request_id and request_id in self.active_requests:
                del self.active_requests[request_id]

    def classify_openai_error(self, error_detail: Any) -> str:
        error_str = str(error_detail).lower()
        if "unsupported_country_region_territory" in error_str or "country, region, or territory not supported" in error_str:
            return "OpenAI API is not available in your region. Consider using a VPN or Azure OpenAI service."
        if "invalid_api_key" in error_str or "unauthorized" in error_str:
            return "Invalid API key. Please check your provider configuration."
        if "rate_limit" in error_str or "quota" in error_str:
            return "Rate limit exceeded. Please wait and try again, or upgrade your API plan."
        if "model" in error_str and ("not found" in error_str or "does not exist" in error_str):
            return "Model not found. Please check your provider model mapping configuration."
        if "billing" in error_str or "payment" in error_str:
            return "Billing issue. Please check your upstream provider billing status."
        return str(error_detail)

    def cancel_request(self, request_id: str) -> bool:
        if request_id in self.active_requests:
            self.active_requests[request_id].set()
            return True
        return False


class OpenAIClientRegistry:
    def __init__(self, proxy_config):
        self.proxy_config = proxy_config
        self._clients: Dict[str, OpenAIClient] = {}

    def get_client(self, provider_name: str) -> OpenAIClient:
        if provider_name in self._clients:
            return self._clients[provider_name]

        provider = self.proxy_config.get_provider(provider_name)
        if not provider:
            raise ValueError(f"Provider not found: {provider_name}")

        default_model = provider["model_map"].get("big") or provider["model_map"].get("middle") or provider["model_map"].get("small")
        client = OpenAIClient(
            provider["api_key"],
            provider["base_url"],
            default_model,
            self.proxy_config.request_timeout,
            api_version=provider.get("api_version"),
            custom_headers=provider.get("custom_headers"),
        )
        self._clients[provider_name] = client
        return client


client_registry = OpenAIClientRegistry(config)
