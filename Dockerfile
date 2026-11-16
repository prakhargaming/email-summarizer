FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev
COPY alembic.ini ./
COPY app ./app
RUN useradd --system --uid 10001 app
USER app
EXPOSE 8000
# The digest job runs this same image: python -m app.jobs.run_digest
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
