import logging

from fastapi import FastAPI

from app.api import gmail, items, push, rules, runs, settings

# The digest's JSON logs are INFO; uvicorn leaves the root logger at WARNING.
logging.getLogger("digest").addHandler(logging.StreamHandler())
logging.getLogger("digest").setLevel(logging.INFO)

app = FastAPI()
app.include_router(gmail.router)
app.include_router(items.router)
app.include_router(push.router)
app.include_router(rules.router)
app.include_router(runs.router)
app.include_router(settings.router)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}
