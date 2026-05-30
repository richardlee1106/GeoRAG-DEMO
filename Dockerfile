# =============================================================================
# GeoRAG-demo — RAG Web UI with SearXNG + content extraction
# =============================================================================
# Build:    docker build -t georag-demo .
# Run:      docker run -p 8000:8000 georag-demo
# Run+GPU:  docker run -p 8000:8000 \
#             -e LLM_API=http://host.docker.internal:8080/v1 \
#             -e SEARXNG_URL=http://host.docker.internal:8081/search \
#             georag-demo
# =============================================================================

FROM python:3.11-slim

LABEL maintainer="GeoRAG" \
      description="GeoRAG-demo — RAG Web UI with SearXNG search & content extraction"

# ── System dependencies for Playwright (Firefox) ────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ───────────────────────────────────────────────────────
WORKDIR /app

# ── Python dependencies ─────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Install Playwright browsers (Firefox for anti-crawl fallback) ───────────
RUN python3 -m playwright install firefox \
    && python3 -m playwright install-deps firefox

# ── Application code ────────────────────────────────────────────────────────
COPY main.py .
COPY eco_engine.py .
COPY browser_session.py .
COPY templates/ templates/

# ── Runtime config ──────────────────────────────────────────────────────────
EXPOSE 8000

ENV LLM_API=http://llm:8080/v1
ENV EMBED_API=http://embed:8082/v1
ENV RERANK_API=http://rerank:8083/v1
ENV SEARXNG_URL=http://searxng:8081/search

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
