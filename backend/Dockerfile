FROM ghcr.io/astral-sh/uv:python3.11-bookworm

WORKDIR /app/backend

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen

COPY backend ./

ENV PATH="/app/backend/.venv/bin:$PATH"

CMD ["uv", "run", "andes-api"]
