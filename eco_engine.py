import sys
import os
import subprocess
import trafilatura

# MarkItDown 可选导入
try:
    from markitdown import MarkItDown
    _HAS_MARKITDOWN = True
except ImportError:
    _HAS_MARKITDOWN = False

# Playwright 可选导入（用于 403 回退）
_PW_BROWSER = None  # 全局复用浏览器实例
_PW_INSTANCE = None  # 全局 Playwright 实例（防止GC）

try:
    from playwright.sync_api import sync_playwright
    _HAS_PLAYWRIGHT = True
except ImportError:
    _HAS_PLAYWRIGHT = False

def _get_pw_browser():
    """获取/创建全局 Playwright 浏览器实例（复用）"""
    global _PW_BROWSER, _PW_INSTANCE
    if _PW_BROWSER is None and _HAS_PLAYWRIGHT:
        try:
            _PW_INSTANCE = sync_playwright().start()
            _PW_BROWSER = _PW_INSTANCE.chromium.launch(headless=True)
        except:
            pass
    return _PW_BROWSER

def _pw_fetch_html(url):
    """用全局 Playwright 浏览器下载页面 HTML"""
    browser = _get_pw_browser()
    if not browser:
        return None
    try:
        ctx = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0'
        )
        page = ctx.new_page()
        page.goto(url, timeout=20000, wait_until='domcontentloaded')
        page.wait_for_timeout(1000)
        html = page.content()
        page.close()
        ctx.close()
        return html
    except:
        return None

def get_content_density(text):
    if not text: return 0
    stripped = text.strip()
    if not stripped: return 0
    return len(stripped) / len(text)

def firecrawl_scrape(url):
    # 使用 Firecrawl 作為專業備案
    try:
        # 從 .env 或環境變數獲取 Key (這裡假設已載入或手動處理)
        # 為了穩定性，我們直接調用 npx firecrawl
        cmd = [
            "npx", "firecrawl", "scrape", url,
            "--wait-for", "5000",
            "--only-main-content"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout
    except Exception as e:
        print(f"Firecrawl failed: {e}", file=sys.stderr)
    return None

def smart_extract(target):
    # 1. 嘗試 trafilatura
    content = ""
    try:
        if os.path.exists(target):
            with open(target, 'r', encoding='utf-8') as f:
                content = f.read()
        else:
            # 判斷是否為高防護網站或需要 JS 渲染
            # 如果是政府網站或特定的 SPA，優先考慮 Firecrawl
            if "ndc.gov.tw" in target or "firecrawl" in os.environ.get("PREFER_CRAWLER", ""):
                print("Using Firecrawl for high-security/SPA site...", file=sys.stderr)
                content = firecrawl_scrape(target)
                if content: return content

            content = trafilatura.fetch_url(target)
            if content:
                content = trafilatura.extract(content)
            
            # trafilatura 失败时回退到 Playwright（处理反爬，复用全局浏览器）
            if not content and _HAS_PLAYWRIGHT and not os.path.exists(target):
                try:
                    html = _pw_fetch_html(target)
                    if html:
                        content = trafilatura.extract(html)
                except:
                    pass
        
        if get_content_density(content) < 0.2 and _HAS_MARKITDOWN:
            # 2. 切換到 MarkItDown
            print("Content density low, switching to MarkItDown...", file=sys.stderr)
            try:
                md = MarkItDown()
                content = md.convert(target).text_content
            except:
                pass
            
    except Exception as e:
        # 3. 最終防線：Firecrawl (如果之前沒用過)
        if not content and not os.path.exists(target):
            print("Fallback to Firecrawl...", file=sys.stderr)
            content = firecrawl_scrape(target)
        
        if not content and _HAS_MARKITDOWN:
            try:
                md = MarkItDown()
                content = md.convert(target).text_content
            except:
                pass
        
    return content

if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = sys.argv[1]
        print(smart_extract(target))
    else:
        print("用法: eco_engine.py <target>")
