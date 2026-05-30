import json
import os
import asyncio
import logging
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader

# Local content extraction engine (eco_engine.py)
from eco_engine import smart_extract
import trafilatura  # used directly for Playwright-fallback extraction

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("rag-webui")

# ─── Configuration (all overridable via env vars for Docker) ──────────────────
LLM_API = os.getenv("LLM_API", "http://127.0.0.1:8080/v1")
EMBED_API = os.getenv("EMBED_API", "http://127.0.0.1:8082/v1")
RERANK_API = os.getenv("RERANK_API", "http://127.0.0.1:8083/v1")
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://127.0.0.1:8081/search")

MAX_SEARCH_RESULTS = 8
MAX_EXTRACT_CHARS = 3000
MAX_CONTEXT_CHARS = 8000

jinja_env = Environment(loader=FileSystemLoader("templates"))

# ─── SSE status helper ──────────────────────────────────────────────────────

def sse_status(stage: str, message: str, detail: str = ""):
    """Yield a status SSE event with console-style logging."""
    logger.info(f"[{stage}] {message} {detail}")
    return f"data: {json.dumps({'type': 'status', 'stage': stage, 'message': message, 'detail': detail})}\n\n"


# ─── RAG pipeline (async generator with status events) ──────────────────────

async def rag_search_and_process(query: str):
    """
    Full RAG pipeline as async generator.
    Yields SSE status events, yields the final context string via a special event.
    """
    t_start = time.time()
    logger.info("═" * 50)
    logger.info(f"🚀 RAG 管道启动 — 查询: '{query}'")
    logger.info("═" * 50)

    # ── Step 1: SearXNG ──────────────────────────────────────────────────
    logger.info("─" * 40)
    logger.info("🔍 阶段 1/3: SearXNG 联网搜索")
    logger.info("─" * 40)
    yield sse_status("searching", "🔍 正在调用联网搜索...", "SearXNG")

    t1 = time.time()
    results = await search_web(query)
    t_search = time.time() - t1

    if not results:
        logger.warning("⚠️  SearXNG 搜索返回空结果")
        yield sse_status("searching", "⚠️  SearXNG 搜索无结果", f"耗时 {t_search:.1f}s")
        yield sse_status("done_no_context", "未获取到联网内容，直接回答", "")
        return  # No context, skip to direct answer
    else:
        logger.info(f"✅ SearXNG 返回 {len(results)} 条结果 ({t_search:.1f}s)")
        for i, r in enumerate(results[:5], 1):
            logger.info(f"   [{i}] {r['title'][:60]}")
            logger.info(f"       {r['url'][:80]}")
        if len(results) > 5:
            logger.info(f"   ... 还有 {len(results)-5} 条")
        yield sse_status("searching", f"✅ SearXNG 搜索完成", f"获取 {len(results)} 条结果，耗时 {t_search:.1f}s")

    # ── Step 2: save-your-token (content extraction) ──────────────────────
    logger.info("─" * 40)
    logger.info("📄 阶段 2/3: save-your-token 内容提取")
    logger.info("─" * 40)
    yield sse_status("extracting", "📄 正在提取网页内容...", "save-your-token (Trafilatura)")

    t2 = time.time()
    extract_tasks = [extract_content(r["url"]) for r in results]
    extracted = await asyncio.gather(*extract_tasks)
    t_extract = time.time() - t2

    docs_with_content = []
    for r, content in zip(results, extracted):
        if content and content.strip():
            docs_with_content.append({
                "title": r["title"],
                "url": r["url"],
                "content": content,
            })

    if not docs_with_content:
        logger.warning("⚠️  save-your-token 未能提取有效内容，改用搜索摘要")
        for r in results:
            if r["content"].strip():
                docs_with_content.append({
                    "title": r["title"],
                    "url": r["url"],
                    "content": r["content"][:MAX_EXTRACT_CHARS],
                })

    if not docs_with_content:
        logger.warning("⚠️  无可用内容，跳过 RAG")
        yield sse_status("extracting", "⚠️  内容提取失败，无可用来源", "")
        yield sse_status("done_no_context", "无内容来源，直接回答", "")
        return

    logger.info(f"✅ save-your-token 提取 {len(docs_with_content)} 页内容 ({t_extract:.1f}s)")
    for i, d in enumerate(docs_with_content, 1):
        logger.info(f"   [{i}] {d['title'][:50]} ({len(d['content'])} chars)")
    yield sse_status("extracting", f"✅ 内容提取完成", f"来自 {len(docs_with_content)} 个网页，耗时 {t_extract:.1f}s")

    # ── Step 3: Build context directly (skip embedding & reranker) ──────
    logger.info("─" * 40)
    logger.info("📋 阶段 3/3: 构建 RAG 上下文")
    logger.info("─" * 40)
    yield sse_status("building_ctx", "📋 正在整理上下文...", "")

    context_parts = []
    total_chars = 0
    for d in docs_with_content:
        header = f"来源: {d['title']}\nURL: {d['url']}\n\n"
        body = d["content"]
        entry = header + body + "\n\n---\n\n"
        if total_chars + len(entry) > MAX_CONTEXT_CHARS:
            remaining = MAX_CONTEXT_CHARS - total_chars
            if remaining > len(header) + 100:
                entry = header + body[:remaining - len(header) - 50] + "...\n\n---\n\n"
            else:
                break
        context_parts.append(entry)
        total_chars += len(entry)

    context = "".join(context_parts)
    t_total = time.time() - t_start
    logger.info("═" * 50)
    logger.info(f"✅ RAG 管道完成 — {len(context_parts)} 个来源, {total_chars} chars")
    logger.info(f"   总耗时: {t_total:.1f}s")
    logger.info("═" * 50)
    yield sse_status("building_ctx", f"✅ 上下文构建完成", f"{len(context_parts)} 个来源, {t_total:.1f}s")
    yield sse_status("generating", "💬 正在生成回答...", "Bonsai-8B (LLM)")

    # Yield source info (titles + URLs) for the frontend to display at bubble end
    sources_info = [{"title": d["title"], "url": d["url"]} for d in docs_with_content]
    yield f"data: {json.dumps({'type': 'sources', 'sources': sources_info})}\n\n"

    # Return context via a special event so the caller can capture it
    yield f"data: {json.dumps({'type': 'rag_context', 'context': context})}\n\n"


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def search_web(query: str) -> list[dict]:
    """Search via SearXNG, return list of {title, url, content}."""
    params = {"q": query, "format": "json", "language": "zh-CN"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(SEARXNG_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            seen = set()
            unique = []
            for r in results:
                url = r.get("url", "")
                if url and url not in seen:
                    seen.add(url)
                    unique.append({
                        "title": r.get("title", ""),
                        "url": url,
                        "content": r.get("content", ""),
                    })
            return unique[:MAX_SEARCH_RESULTS]
    except Exception as e:
        logger.warning(f"❌ SearXNG search failed: {e}")
        return []


async def extract_content(url: str) -> str:
    """
    Extract readable content from a URL.
    1st try: save-your-token's smart_extract (trafilatura + fallbacks)
    2nd try: Playwright browser with persistent login state (反爬绕过)
    """
    loop = asyncio.get_running_loop()

    # Try 1: standard smart_extract
    try:
        content = await loop.run_in_executor(None, smart_extract, url)
        if content and len(content.strip()) > 100:
            logger.info(f"   ✅ smart_extract 提取成功 ({len(content)} chars)")
            return content[:MAX_EXTRACT_CHARS]
    except Exception as e:
        logger.debug(f"   smart_extract failed for {url}: {e}")

    # Try 2: Playwright browser with login cookies
    try:
        logger.info(f"   🔄 回退到 Playwright 浏览器提取: {url[:60]}")
        from browser_session import fetch_page
        html = await loop.run_in_executor(None, fetch_page, url, 20)
        if html and len(html) > 500:
            # Use trafilatura to extract clean text from the rendered HTML
            content = await loop.run_in_executor(None, lambda: trafilatura.extract(html))
            if content and len(content.strip()) > 100:
                logger.info(f"   ✅ Playwright 提取成功 ({len(content)} chars)")
                return content[:MAX_EXTRACT_CHARS]
            elif html:
                # Fallback: strip HTML tags manually
                import re
                text = re.sub(r'<[^>]+>', '', html)
                text = re.sub(r'\s+', ' ', text).strip()
                if len(text) > 100:
                    logger.info(f"   ✅ Playwright 提取 (降级纯文本) ({len(text)} chars)")
                    return text[:MAX_EXTRACT_CHARS]
    except ImportError:
        logger.info("   ⚠️  browser_session 未安装，跳过 Playwright 回退")
    except Exception as e:
        logger.debug(f"   Playwright extraction failed for {url}: {e}")

    return ""


async def get_embedding(text: str) -> list[float]:
    """Get embedding vector from jina model."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{EMBED_API}/embeddings",
                json={"input": text, "model": "jina"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data["data"][0]["embedding"]
    except Exception as e:
        logger.warning(f"Embedding failed: {e}")
        return []


async def rerank(query: str, documents: list[str]) -> list[int]:
    """Rerank documents with BGE model, return sorted indices (best first)."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{RERANK_API}/rerank",
                json={"model": "bge", "query": query, "documents": documents},
            )
            resp.raise_for_status()
            data = resp.json()
            results = sorted(data["results"], key=lambda x: x["relevance_score"], reverse=True)
            return [r["index"] for r in results]
    except Exception as e:
        logger.warning(f"Rerank failed: {e}")
        return list(range(len(documents)))


async def stream_chat(messages: list[dict], model: str = "bonsai"):
    """Send chat request to LLM, yield content chunks."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{LLM_API}/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "stream": True,
                "max_tokens": 2048,
                "temperature": 0.7,
            },
        )
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except json.JSONDecodeError:
                    continue


# ─── FastAPI App ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("╔══════════════════════════════════════════════╗")
    logger.info("║     🪴 Bonsai Web AI 启动中...               ║")
    logger.info("╚══════════════════════════════════════════════╝")
    services = {
        "LLM Bonsai-8B      ": (LLM_API, "/models"),
        "Embedding jina-v5  ": (EMBED_API, "/models"),
        "Reranker bge-v2-m3 ": (RERANK_API, "/models"),
    }
    all_ok = True
    for name, (url, path) in services.items():
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(f"{url}{path}")
                ok = r.status_code == 200
                logger.info(f"  {'✅' if ok else '❌'} {name}")
                if not ok:
                    all_ok = False
        except Exception as e:
            logger.warning(f"  ⚠️  {name} 不可用: {e}")
            all_ok = False
    if all_ok:
        logger.info("所有后端服务就绪 ✅")
    yield

app = FastAPI(title="Bonsai Web AI — RAG Demo", lifespan=lifespan)


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    template = jinja_env.get_template("index.html")
    return HTMLResponse(template.render())


@app.post("/api/chat")
async def chat(request: Request):
    """
    Chat endpoint with SSE streaming and RAG pipeline.
    Body: { "message": str, "web_search": bool, "history": list }
    """
    body = await request.json()
    user_message = body.get("message", "")
    web_search = body.get("web_search", False)
    history = body.get("history", [])

    async def generate():
        rag_context = ""
        llm_started = False

        if web_search and user_message.strip():
            # ── Run RAG pipeline (generator yields status events) ───
            async for event in rag_search_and_process(user_message):
                # Check if this is the context payload event
                if '"type": "rag_context"' in event:
                    try:
                        payload = json.loads(event[6:].strip())
                        rag_context = payload.get("context", "")
                    except Exception:
                        pass
                else:
                    yield event

            # ── Done no_context: skip LLM ──
            if not rag_context and any('"done_no_context"' in e for e in [event]):
                # But we still want to answer, just without context
                pass
        else:
            yield sse_status("generating", "💬 正在生成回答...", "Bonsai-8B (LLM)")

        # ── Build system message ──
        system_msg = "你是一个智能AI助手，名叫Bonsai，由PrismML开发。你用中文回答，简洁准确有帮助。"
        if rag_context:
            system_msg += (
                "\n\n以下是从网页搜索获得的相关参考资料，请基于这些内容回答用户问题。"
                "用中文回答，在适当时引用信息来源。如果参考资料不足以回答，如实告知。\n\n"
                f"参考资料：\n{rag_context}"
            )

        messages = [{"role": "system", "content": system_msg}]
        for h in history[-10:]:
            messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": user_message})

        logger.info(f"💬 LLM 请求: system={len(system_msg)}ch, history={len(history)}turns, user={len(user_message)}ch")

        # ── Stream LLM response ──
        t_llm = time.time()
        full_content = ""
        token_count = 0
        async for chunk in stream_chat(messages):
            if chunk:
                full_content += chunk
                token_count += 1
                yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"

        t_llm_took = time.time() - t_llm
        logger.info(f"✅ LLM 生成完成: {token_count} tokens, {t_llm_took:.1f}s ({token_count/t_llm_took:.1f} tok/s)")

        yield f"data: {json.dumps({'type': 'done', 'content': full_content})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/health")
async def health():
    """Check all backend services health."""
    results = {}
    services = {
        "llm": (LLM_API, "/models"),
        "embedding": (EMBED_API, "/models"),
        "reranker": (RERANK_API, "/models"),
        "searxng": ("http://127.0.0.1:8081", "/")  # 只检查端口存活,
    }
    for name, (base, path) in services.items():
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(f"{base}{path}")
                results[name] = "ok" if r.status_code == 200 else f"status_{r.status_code}"
        except Exception as e:
            results[name] = f"error: {type(e).__name__}"
    return JSONResponse(results)

@app.get("/favicon.ico")
async def favicon():
    from fastapi.responses import Response
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="30" fill="%234facfe"/><text x="32" y="40" text-anchor="middle" font-size="28" fill="white">B</text></svg>'
    return Response(content=svg, media_type="image/svg+xml")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
