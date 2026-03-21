import json
import os
import sys
from typing import Any, Dict, List, Optional


class Config:
    def __init__(self):
        self.host = os.environ.get("HOST", "0.0.0.0")
        self.port = int(os.environ.get("PORT", "8082"))
        self.log_level = os.environ.get("LOG_LEVEL", "INFO")
        self.max_tokens_limit = int(os.environ.get("MAX_TOKENS_LIMIT", "4096"))
        self.min_tokens_limit = int(os.environ.get("MIN_TOKENS_LIMIT", "100"))
        self.request_timeout = int(os.environ.get("REQUEST_TIMEOUT", "90"))
        self.max_retries = int(os.environ.get("MAX_RETRIES", "2"))

        self.openai_api_key = os.environ.get("OPENAI_API_KEY")
        self.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
        self.openai_base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.azure_api_version = os.environ.get("AZURE_API_VERSION")
        self.big_model = os.environ.get("BIG_MODEL", "gpt-4o")
        self.middle_model = os.environ.get("MIDDLE_MODEL", self.big_model)
        self.small_model = os.environ.get("SMALL_MODEL", "gpt-4o-mini")
        self.proxy_config_json = os.environ.get("PROXY_CONFIG_JSON")

        self.raw_proxy_config = self._load_proxy_config_json()
        self.providers = self._load_providers()
        self.providers_by_name = {provider["name"]: provider for provider in self.providers}
        self._validate_provider_model_uniqueness()
        self.client_tokens = self._load_client_tokens()
        self.client_token_set = set(self.client_tokens)
        self.logging_config = self._load_logging_config()

        if not self.providers:
            raise ValueError("At least one provider must be configured")

        if not self.get_default_provider():
            raise ValueError("Default provider is not configured")

    def _load_proxy_config_json(self) -> Dict[str, Any]:
        if not self.proxy_config_json:
            return {}

        try:
            parsed = json.loads(self.proxy_config_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid PROXY_CONFIG_JSON: {exc}") from exc

        if not isinstance(parsed, dict):
            raise ValueError("PROXY_CONFIG_JSON must be a JSON object")

        if "model_routing" in parsed:
            raise ValueError(
                "PROXY_CONFIG_JSON.model_routing is no longer supported; route by providers[].model_map values instead"
            )

        return parsed

    def _load_providers(self) -> List[Dict[str, Any]]:
        configured_providers = self.raw_proxy_config.get("providers")
        if configured_providers is None:
            provider = self._build_legacy_provider()
            return [provider] if provider else []

        if not isinstance(configured_providers, list):
            raise ValueError("providers must be a list")

        providers: List[Dict[str, Any]] = []
        for provider in configured_providers:
            providers.append(self._normalize_provider(provider))
        return providers

    def _build_legacy_provider(self) -> Optional[Dict[str, Any]]:
        if not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY not found in environment variables")

        return {
            "name": "default",
            "platform": "azure" if self.azure_api_version else "openai",
            "base_url": self.openai_base_url,
            "api_key": self.openai_api_key,
            "api_version": self.azure_api_version,
            "model_map": {
                "big": self.big_model,
                "middle": self.middle_model,
                "small": self.small_model,
            },
            "custom_headers": self.get_custom_headers(),
        }

    def _normalize_provider(self, provider: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(provider, dict):
            raise ValueError("Each provider must be an object")

        name = provider.get("name")
        api_key = provider.get("api_key")
        base_url = provider.get("base_url")
        if not name or not api_key or not base_url:
            raise ValueError("Each provider requires name, api_key, and base_url")

        model_map = provider.get("model_map") or {}
        if not isinstance(model_map, dict):
            raise ValueError("provider.model_map must be an object")

        custom_headers = provider.get("custom_headers") or {}
        if not isinstance(custom_headers, dict):
            raise ValueError("provider.custom_headers must be an object")

        return {
            "name": name,
            "platform": provider.get("platform") or "openai",
            "base_url": base_url,
            "api_key": api_key,
            "api_version": provider.get("api_version"),
            "model_map": {
                "big": model_map.get("big", self.big_model),
                "middle": model_map.get("middle", model_map.get("big", self.middle_model)),
                "small": model_map.get("small", self.small_model),
            },
            "custom_headers": {str(key): str(value) for key, value in custom_headers.items()},
        }

    def _validate_provider_model_uniqueness(self) -> None:
        seen_models: Dict[str, str] = {}
        for provider in self.providers:
            provider_name = provider["name"]
            for model_name in provider.get("model_map", {}).values():
                if not model_name:
                    continue
                existing_provider = seen_models.get(model_name)
                if existing_provider and existing_provider != provider_name:
                    raise ValueError(
                        f"Duplicate model_map value '{model_name}' found in providers '{existing_provider}' and '{provider_name}'"
                    )
                seen_models[model_name] = provider_name

    def _load_client_tokens(self) -> List[str]:
        configured_tokens = self.raw_proxy_config.get("client_tokens")
        if configured_tokens is None:
            if not self.anthropic_api_key:
                return []
            return [self.anthropic_api_key]

        if not isinstance(configured_tokens, list):
            raise ValueError("client_tokens must be a list")

        normalized_tokens = []
        for token in configured_tokens:
            if isinstance(token, dict):
                raise ValueError("client_tokens must be a list of strings")
            if not isinstance(token, str) or not token:
                raise ValueError("Each client token must be a non-empty string")
            normalized_tokens.append(token)
        return normalized_tokens

    def _load_logging_config(self) -> Dict[str, Any]:
        configured_logging = self.raw_proxy_config.get("logging") or {}
        if not isinstance(configured_logging, dict):
            raise ValueError("logging must be an object")

        return {
            "log_full_input_output": configured_logging.get("log_full_input_output", True),
            "max_image_log_chars": int(configured_logging.get("max_image_log_chars", 2000)),
            "mask_token_in_logs": configured_logging.get("mask_token_in_logs", True),
        }

    def validate_api_key(self) -> bool:
        if not self.providers:
            return False
        return all(bool(provider.get("api_key")) for provider in self.providers)

    def validate_client_api_key(self, client_api_key: Optional[str]) -> Optional[Dict[str, Any]]:
        if not self.client_tokens:
            return {
                "client_token_masked": None,
                "auth_mode": "anonymous",
            }

        if not client_api_key:
            return None

        if client_api_key not in self.client_token_set:
            return None

        return {
            "client_token_masked": self.mask_token(client_api_key),
            "auth_mode": "token",
        }

    def get_custom_headers(self) -> Dict[str, str]:
        custom_headers = {}
        for env_key, env_value in dict(os.environ).items():
            if env_key.startswith("CUSTOM_HEADER_"):
                header_name = env_key[14:]
                if header_name:
                    custom_headers[header_name.replace("_", "-")] = env_value
        return custom_headers

    def get_provider(self, provider_name: str) -> Optional[Dict[str, Any]]:
        return self.providers_by_name.get(provider_name)

    def get_providers_summary(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": provider["name"],
                "platform": provider["platform"],
                "base_url": provider["base_url"],
            }
            for provider in self.providers
        ]

    def get_default_provider_name(self) -> str:
        return self.providers[0]["name"]

    def get_default_provider(self) -> Optional[Dict[str, Any]]:
        return self.providers[0] if self.providers else None

    def get_logging_config(self) -> Dict[str, Any]:
        return self.logging_config

    def is_client_auth_enabled(self) -> bool:
        return bool(self.client_tokens)

    def mask_token(self, token: Optional[str]) -> Optional[str]:
        if not token:
            return None
        if not self.logging_config.get("mask_token_in_logs", True):
            return token
        if len(token) <= 8:
            return "*" * len(token)
        return f"{token[:4]}...{token[-4:]}"


try:
    config = Config()
    print(
        f"Configuration loaded: providers={len(config.providers)}, default_provider='{config.get_default_provider_name()}'"
    )
except Exception as e:
    print(f"Configuration Error: {e}")
    sys.exit(1)
