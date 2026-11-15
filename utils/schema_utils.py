"""Helpers for presenting Pydantic schemas to LLM tools."""

from typing import Any

from pydantic import BaseModel


def get_clean_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return a token-efficient JSON schema using the centralized cleaner."""
    from services.agent.tool_schema import sanitize_parameters

    return sanitize_parameters(model.model_json_schema())
