r"""诊断 X 页面状态：当前标签 URL、推文元素数、登录态、首条推文内容样例。"""
import json
import sys

from chrome_mcp import McpClient

JS = """
const stripQ = u => String(u).split('?')[0];
const articles = document.querySelectorAll('article[data-testid="tweet"]');
const first = articles[0];
return JSON.stringify({
  url: stripQ(location.href),
  articleCount: articles.length,
  hasLoginButton: !!document.querySelector('a[data-testid="loginButton"], [data-testid="loginButton"]'),
  firstTweet: first ? {
    text: ((first.querySelector('[data-testid="tweetText"]') || {}).innerText || '').slice(0, 120)
  } : null
}, null, 2);
"""

cli = McpClient()
cli.ensure()
r = cli.call("chrome_javascript", {"code": JS})
content = (r.get("result", {}) or {}).get("content", [])
for block in content:
    if block.get("type") == "text":
        print(block.get("text", ""))
print("--- raw result ---")
print(json.dumps(r, ensure_ascii=False)[:600])
