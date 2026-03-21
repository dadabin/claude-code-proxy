from typing import Any, Dict, Optional

from src.core.config import config


class ModelManager:
    def __init__(self, config):
        self.config = config

    def map_claude_model_to_openai(self, claude_model: str) -> str:
        return self.resolve_route(claude_model)["target_model"]

    def resolve_route(self, requested_model: str) -> Dict[str, Any]:
        direct_match_provider = self._find_provider_by_model_value(requested_model)
        if direct_match_provider:
            return self._build_route_result(
                requested_model=requested_model,
                provider=direct_match_provider,
                target_model=requested_model,
                route_type="direct_match",
            )

        provider = self.config.get_default_provider()
        if not provider:
            raise ValueError("Default provider is not configured")

        size_key = self._classify_claude_model_size(requested_model)
        if not size_key:
            raise ValueError(
                f"Unsupported model '{requested_model}'. Model must exactly match a configured providers[].model_map value or contain one of: haiku, sonnet, opus"
            )

        target_model = provider["model_map"].get(size_key) or provider["model_map"].get("big")
        return self._build_route_result(
            requested_model=requested_model,
            provider=provider,
            target_model=target_model,
            route_type="default_alias",
        )

    def _find_provider_by_model_value(self, requested_model: str) -> Optional[Dict[str, Any]]:
        for provider in self.config.providers:
            if requested_model in provider.get("model_map", {}).values():
                return provider
        return None

    def _classify_claude_model_size(self, claude_model: str) -> Optional[str]:
        model_lower = claude_model.lower()
        if "haiku" in model_lower:
            return "small"
        if "sonnet" in model_lower:
            return "middle"
        if "opus" in model_lower:
            return "big"
        return None

    def _build_route_result(
        self, requested_model: str, provider: Dict[str, Any], target_model: str, route_type: str
    ) -> Dict[str, Any]:
        return {
            "requested_model": requested_model,
            "provider_name": provider["name"],
            "platform": provider["platform"],
            "base_url": provider["base_url"],
            "api_version": provider.get("api_version"),
            "custom_headers": provider.get("custom_headers", {}),
            "target_model": target_model,
            "route_type": route_type,
        }


model_manager = ModelManager(config)
