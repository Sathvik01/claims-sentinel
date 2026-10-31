"""LLM access through LangChain's ChatGroq: structured (Pydantic) output only."""

from __future__ import annotations

import base64
import json
from functools import lru_cache
from typing import TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from pydantic import BaseModel

from app.config import settings

T = TypeVar("T", bound=BaseModel)
PROVIDER = "Groq"


class LLMUnavailable(RuntimeError):
    pass


def available() -> bool:
    return bool(settings.groq_api_key)


@lru_cache(maxsize=4)
def _chat(model: str) -> ChatGroq:
    return ChatGroq(model_name=model, groq_api_key=settings.groq_api_key, temperature=0.1,
                    max_retries=3, request_timeout=settings.llm_timeout_s)


def generate(schema: type[T], content: str | list, system: str, *, vision: bool = False) -> T:
    """Text goes to the structured-output model; images to the vision model in JSON mode."""
    if not available():
        raise LLMUnavailable("GROQ_API_KEY is not configured")
    if vision:
        model, method = settings.groq_vision_model, "json_mode"
        system += ("\nRespond only with a JSON object that validates against this JSON schema:\n"
                   + json.dumps(schema.model_json_schema()))
    else:
        model, method = settings.groq_model, "json_schema"
    llm = _chat(model).with_structured_output(schema, method=method)
    out = llm.invoke([SystemMessage(system), HumanMessage(content=content)])
    if out is None:
        raise RuntimeError(f"{model} returned no structured output")
    return out


def image_part(data: bytes, mime_type: str) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64.b64encode(data).decode()}"}}
