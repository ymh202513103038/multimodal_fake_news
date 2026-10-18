r"""诊断目标站点的链接形状：列出页面上所有 href 的模式样例。"""
import re
import sys

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
PROXIES = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}

url = sys.argv[1]
use_proxy = len(sys.argv) > 2 and sys.argv[2] == "proxy"
r = requests.get(url, timeout=30, headers=UA, proxies=PROXIES if use_proxy else None)
if r.encoding in (None, "ISO-8859-1"):
    r.encoding = r.apparent_encoding
print(f"status={r.status_code} len={len(r.text)} apparent={r.apparent_encoding}")

hrefs = re.findall(r'href="([^"]+)"', r.text)
from collections import Counter
shapes = Counter()
samples = {}
for h in hrefs:
    shape = re.sub(r"[0-9a-zA-Z\u4e00-\u9fff]{4,}", "X", h)
    shapes[shape] += 1
    samples.setdefault(shape, h)
for shape, n in shapes.most_common(15):
    print(f"{n:3d}  {shape}   例: {samples[shape][:90]}")
