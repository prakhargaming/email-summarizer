from app.core.config import settings
from app.llm import client

r = client().models.generate_content(model=settings.llm_model, contents="Reply with OK")
u = r.usage_metadata
print("via:", settings.llm_base_url or "direct Gemini")
print("finish_reason:", r.candidates[0].finish_reason if r.candidates else None)
print("text:", r.text)
print("tokens: prompt", u.prompt_token_count, "output", u.candidates_token_count, "total", u.total_token_count)
