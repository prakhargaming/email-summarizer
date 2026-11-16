from functools import cache

from google import genai
from google.genai import types
from pydantic import BaseModel

from app.core.config import settings


class LLMBlocked(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@cache
def client() -> genai.Client:
    if settings.llm_base_url:
        return genai.Client(
            api_key=settings.llm_api_key,
            http_options=types.HttpOptions(base_url=settings.llm_base_url, api_version="v1beta"),
        )
    return genai.Client(api_key=settings.gemini_api_key)


def generate_json(system_instruction: str, contents: str, schema: type[BaseModel]) -> tuple[str, dict]:
    """Returns the raw JSON text (caller validates against `schema`) and token usage counts."""
    r = client().models.generate_content(
        model=settings.llm_model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=schema,
            # No tools are ever given; also turn off the SDK's automatic function calling loop.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )
    if r.prompt_feedback and r.prompt_feedback.block_reason:
        raise LLMBlocked(f"prompt_{r.prompt_feedback.block_reason.name.lower()}")
    if not r.candidates:
        raise LLMBlocked("no_candidates")
    finish = r.candidates[0].finish_reason
    if finish != types.FinishReason.STOP:
        raise LLMBlocked(f"finish_{finish.name.lower() if finish else 'none'}")
    if not r.text:
        raise LLMBlocked("empty_text")
    u = r.usage_metadata
    usage = {
        "prompt_tokens": u.prompt_token_count if u else None,
        "output_tokens": u.candidates_token_count if u else None,
    }
    return r.text, usage
