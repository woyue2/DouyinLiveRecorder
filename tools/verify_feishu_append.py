# -*- encoding: utf-8 -*-
"""驗證飛書「追加內容塊」權限是否開通。

用途：直播時間軸在收播時先建好，投稿成功後要把 BV 號補進同一篇文檔；
那一步需要應用具備 docx:document（或 docx:document:write_only）權限。

    python3 tools/verify_feishu_append.py

會建一篇臨時文檔 → 追加一行 → 讀回確認 → 刪除，不會留下垃圾。
"""
import sys

sys.path.insert(0, ".")
import httpx

from src.feishu_uploader import FeishuDocManager, FEISHU_HOST

import configparser

cp = configparser.RawConfigParser()
cp.read("config/local_secrets.ini", encoding="utf-8-sig")
cp2 = configparser.RawConfigParser()
cp2.read("config/config.ini", encoding="utf-8-sig")


def cfg(sec, key, default=""):
    for c in (cp, cp2):
        if c.has_option(sec, key):
            return c.get(sec, key)
    return default


app_id = cfg("飞书文档配置", "飞书app_id")
app_secret = cfg("飞书文档配置", "飞书app_secret")
folder = cfg("飞书文档配置", "飞书文件夹_token")
mgr = FeishuDocManager(app_id, app_secret, folder_token=folder)

md = "/tmp/_append_test.md"
with open(md, "w", encoding="utf-8") as f:
    f.write("# 追加能力自測\n\n這是一篇臨時文檔，用來驗證追加 API。\n")

url, token = mgr.create_docx_from_markdown(md, doc_title="临时-追加自测")
print("建立:", url, "| token:", (token or "")[:12])
if not token:
    sys.exit("建立失敗")

ok = mgr.append_text_block(token, "已发布到 B 站（自测）：BV1test123（9P）示例标题")
print("追加结果:", ok)

# 驗證：讀回文檔純文字
at = mgr.get_tenant_access_token()
r = httpx.get(f"{FEISHU_HOST}/open-apis/docx/v1/documents/{token}/blocks?page_size=50",
              headers={"Authorization": f"Bearer {at}"}, timeout=15)
data = r.json()
texts = []
for b in (data.get("data") or {}).get("items", []):
    for el in (b.get("text") or {}).get("elements", []) or []:
        tr = el.get("text_run") or {}
        if tr.get("content"):
            texts.append(tr["content"])
print("讀回內容:", texts)

# 清理臨時文檔
r = httpx.delete(f"{FEISHU_HOST}/open-apis/drive/v1/files/{token}?type=docx",
                 headers={"Authorization": f"Bearer {at}"}, timeout=15)
print("刪除結果:", r.json().get("code"), r.json().get("msg"))
os.remove(md)
