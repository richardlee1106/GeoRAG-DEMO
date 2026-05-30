"""
Playwright 浏览器管理器 — 持久化登录态，绕过反爬。

用法：
  1. 首次运行: python3 browser_session.py  # 打开浏览器，手动登录目标网站
  2. 登录完成后按 Ctrl+C，状态自动保存
  3. 后续 eco_engine 自动复用 cookies

存储目录: ~/.pw-bonsai-session/
"""
import os
import json
import time
import atexit
import signal
import logging

logger = logging.getLogger("browser-session")

STATE_DIR = os.path.expanduser("~/.pw-bonsai-session")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
USER_DATA_DIR = os.path.join(STATE_DIR, "firefox-profile")

_PW_INSTANCE = None
_PW_BROWSER = None
_PW_CONTEXT = None


def ensure_state_dir():
    os.makedirs(STATE_DIR, exist_ok=True)


def _cleanup():
    global _PW_INSTANCE, _PW_BROWSER, _PW_CONTEXT
    try:
        if _PW_CONTEXT:
            # Save state before closing
            try:
                _PW_CONTEXT.storage_state(path=STATE_FILE)
                logger.info(f"✅ 浏览器状态已保存到 {STATE_FILE}")
            except Exception as e:
                logger.warning(f"保存状态失败: {e}")
            _PW_CONTEXT.close()
        if _PW_BROWSER:
            _PW_BROWSER.close()
        if _PW_INSTANCE:
            _PW_INSTANCE.stop()
    except Exception:
        pass


def get_browser_context(headless=True):
    """
    获取/创建 Playwright 浏览器上下文（单例、持久化）。
    返回 (context, is_new) — is_new 表示是否首次创建。
    """
    global _PW_INSTANCE, _PW_BROWSER, _PW_CONTEXT

    if _PW_CONTEXT is not None:
        return _PW_CONTEXT, False

    ensure_state_dir()

    try:
        from playwright.sync_api import sync_playwright

        _PW_INSTANCE = sync_playwright().start()
        _PW_BROWSER = _PW_INSTANCE.firefox.launch(
            headless=headless,
        )

        # 尝试加载已保存的状态
        if os.path.exists(STATE_FILE):
            logger.info(f"📂 加载已保存的浏览器状态: {STATE_FILE}")
            _PW_CONTEXT = _PW_BROWSER.new_context(
                storage_state=STATE_FILE,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 720},
                locale="zh-CN",
            )
            return _PW_CONTEXT, False
        else:
            logger.info("🆕 创建新的浏览器会话（首次运行或状态已清除）")
            _PW_CONTEXT = _PW_BROWSER.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 720},
                locale="zh-CN",
            )
            return _PW_CONTEXT, True

    except Exception as e:
        logger.error(f"❌ Playwright 初始化失败: {e}")
        return None, False


def fetch_page(url, timeout=15):
    """
    用浏览器获取页面 HTML（自带 cookie 登录态）。
    返回 HTML 字符串，失败返回 None。
    """
    ctx, _ = get_browser_context()
    if ctx is None:
        return None

    page = None
    try:
        page = ctx.new_page()
        logger.info(f"🌐 浏览器加载: {url[:80]}")
        page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)  # 等 JS 渲染
        html = page.content()
        logger.info(f"✅ 页面加载成功 ({len(html)} bytes)")
        return html
    except Exception as e:
        logger.warning(f"⚠️  浏览器加载失败 {url[:60]}: {type(e).__name__}")
        return None
    finally:
        if page:
            page.close()


def open_login_session():
    """
    打开有头浏览器供用户手动登录。
    用户登录完按 Ctrl+C 结束，状态自动保存。
    """
    print("=" * 60)
    print("🪴 Bonsai Web AI — 浏览器登录助手")
    print("=" * 60)
    print()
    print("正在打开 Firefox 浏览器...")
    print()
    print("请手动完成以下操作：")
    print("  1. 访问 https://www.baidu.com 并登录百度账号")
    print("  2. 访问 https://cn.bing.com （如果需要）")
    print("  3. 访问 https://www.zhihu.com 并登录（可选）")
    print("  4. 访问 https://baike.baidu.com 确认能正常打开")
    print()
    print("完成后按 Ctrl+C 关闭浏览器，登录态会自动保存。")
    print()

    atexit.register(_cleanup)
    signal.signal(signal.SIGINT, lambda s, f: (_cleanup(), exit(0)))
    signal.signal(signal.SIGTERM, lambda s, f: (_cleanup(), exit(0)))

    ctx, is_new = get_browser_context(headless=False)
    if ctx is None:
        print("❌ 无法启动浏览器")
        return

    page = ctx.new_page()
    page.goto("https://www.baidu.com")

    # 保持进程运行
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n正在保存浏览器状态...")
        _cleanup()
        print("✅ 已完成！现在 RAG 查询将使用你的登录态。")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    open_login_session()
