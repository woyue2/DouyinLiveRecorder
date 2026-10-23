import json
import sys
import urllib.request
import datetime

c = json.load(open("cookies.json", encoding="utf-8"))
ci = c.get("cookie_info", {})
pairs = ["%s=%s" % (it["name"], it["value"]) for it in ci.get("cookies", [])]
ck = "; ".join(pairs)

url = ("https://member.bilibili.com/x/web/archives"
       "?status=is_pubing,pubed,not_pubed&pn=1&ps=15&coop=1&interactive=1")
req = urllib.request.Request(url, headers={
    "Cookie": ck,
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 Chrome/120 Safari/537.36"),
    "Referer": "https://member.bilibili.com/platform/upload-manager/article",
})
try:
    r = json.load(urllib.request.urlopen(req, timeout=20))
except Exception as e:
    print("查詢失敗:", type(e).__name__, str(e)[:200])
    sys.exit(1)

print("code:", r.get("code"), r.get("message"))
states = {0: "待审", 1: "已发布", -1: "被驳回", -2: "被退回"}
rows = (r.get("data") or {}).get("arc_audits") or []
if not rows:
    print("  (無資料)")
for v in rows[:12]:
    a = v.get("Archive") or {}
    st = states.get(a.get("state"), a.get("state"))
    ts = datetime.datetime.fromtimestamp(a.get("ctime", 0)).strftime("%m-%d %H:%M")
    print("  %s  %-5s  %s  %s" % (ts, st, a.get("title", "")[:38], a.get("bvid", "")))
