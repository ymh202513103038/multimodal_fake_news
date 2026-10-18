r"""导航到 X 搜索页并统计推文数量（快速验证搜索可用性）。"""
import sys
import time
from urllib.parse import quote

from chrome_mcp import McpClient

query = sys.argv[1] if len(sys.argv) > 1 else "fake news"
tab = sys.argv[2] if len(sys.argv) > 2 else "live"   # live / top

cli = McpClient()
cli.ensure()
url = f"https://x.com/search?q={quote(query)}&f={tab}"
cli.call("chrome_navigate", {"url": url})
time.sleep(6)

js = """
return JSON.stringify({
  articles: document.querySelectorAll('article[data-testid="tweet"]').length,
  timeElements: document.querySelectorAll('time').length,
  bodyMark: (document.body.innerText.match(/No results[^\\n]*/) || [''])[0]
});
"""
r = cli.call("chrome_javascript", {"code": js})
print(r.get("result", {}).get("content", [{}])[0].get("text", "")[:400])
