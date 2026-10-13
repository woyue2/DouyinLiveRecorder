# -*- encoding: utf-8 -*-
"""
將既有網頁文章 (web_posts/*.html) 批次重新匯入飛書雲文檔指定資料夾
憑證一律從 config/config.ini + config/local_secrets.ini 讀取，不寫死於原始碼
用法: python3 tools/migrate_to_feishu_folder.py [--dir web_posts]
"""

import os
import re
import sys
import glob
import html
import time
import argparse
import configparser

_CURRENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_CURRENT_DIR, "src"))

from feishu_uploader import FeishuDocManager  # noqa: E402


def load_feishu_settings():
    config = configparser.RawConfigParser()
    for p in [
        os.path.join(_CURRENT_DIR, "config", "config.ini"),
        os.path.join(_CURRENT_DIR, "config", "local_secrets.ini"),
    ]:
        if os.path.exists(p):
            config.read(p, encoding="utf-8-sig")

    return {
        "app_id": config.get("飞书文档配置", "飞书app_id", fallback="").strip(),
        "app_secret": config.get("飞书文档配置", "飞书app_secret", fallback="").strip(),
        "folder_token": config.get("飞书文档配置", "飞书文件夹_token", fallback="").strip(),
    }


def html_to_markdown(html_path):
    """將精編 HTML 文章反向轉換為 Markdown"""
    with open(html_path, "r", encoding="utf-8", errors="ignore") as fp:
        c = fp.read()

    title_m = re.search(r'<h1 class="article-title">(.*?)</h1>', c)
    title = html.unescape(title_m.group(1).strip()) if title_m else os.path.basename(html_path)

    author_m = re.search(r'<span class="badge">🎙️\s*(.*?)</span>', c)
    author = html.unescape(author_m.group(1).strip()) if author_m else "未分类"

    time_m = re.search(r'<span>📅\s*([\d\-:\s]+)</span>', c)
    pub_time = time_m.group(1).strip() if time_m else ""

    summary_md = ""
    summary_card_m = re.search(r'<div class="summary-card">(.*?)</div>', c, re.DOTALL)
    if summary_card_m:
        raw_card = summary_card_m.group(1)
        clean_card = re.sub(r'<strong>(.*?)</strong>', r'**\1**', raw_card)
        clean_card = re.sub(r'<li>(.*?)</li>', r'- \1', clean_card)
        clean_card = re.sub(r'<p>(.*?)</p>', r'> \1\n', clean_card)
        clean_card = re.sub(r'<[^>]+>', '', clean_card)
        summary_md = clean_card.strip()

    body_m = re.search(r'<div class="article-body">(.*?)</div>\s*<div class="footer-actions">', c, re.DOTALL)
    body_md = body_m.group(1) if body_m else ""
    body_md = re.sub(r'<h2>(.*?)</h2>', r'\n## \1\n', body_md)
    body_md = re.sub(r'<h3>(.*?)</h3>', r'\n### \1\n', body_md)
    body_md = re.sub(r'<img src="(.*?)"[^>]*>', r'\n![](\1)\n', body_md)
    body_md = re.sub(r'<span class="speaker-host">🎙️ 主播</span>', '**主播：**', body_md)
    body_md = re.sub(r'<span class="speaker-guest">💬 提问者</span>', '**提问者：**', body_md)
    body_md = re.sub(r'<strong>(.*?)</strong>', r'**\1**', body_md)
    body_md = re.sub(r'<p>(.*?)</p>', r'\1\n\n', body_md)
    body_md = re.sub(r'<[^>]+>', '', body_md)
    body_md = html.unescape(body_md).strip()

    md = f"# {title}\n\n"
    md += f"> 🎙️ **作者**：{author}  |  📅 **发布时间**：{pub_time}\n\n"
    if summary_md:
        md += f"---\n\n### 💡 核心精彩看点\n\n{summary_md}\n\n---\n\n"
    md += f"{body_md}\n"
    return author, title, md


def main():
    parser = argparse.ArgumentParser(description="批次重新匯入既有文章至飛書雲文檔")
    parser.add_argument("--dir", default=os.path.join(_CURRENT_DIR, "web_posts"), help="HTML 文章目錄")
    args = parser.parse_args()

    cfg = load_feishu_settings()
    if not (cfg["app_id"] and cfg["app_secret"]):
        print("未配置飛書 app_id / app_secret，請檢查 config/config.ini 或 config/local_secrets.ini")
        return 1

    mgr = FeishuDocManager(cfg["app_id"], cfg["app_secret"], folder_token=cfg["folder_token"])

    html_files = sorted(glob.glob(os.path.join(args.dir, "*.html")))
    results = []

    for hf in html_files:
        fname = os.path.basename(hf)
        if fname in ("index.html", "portal.html"):
            continue

        author, title, md_content = html_to_markdown(hf)
        tmp_path = os.path.join("/tmp", f"feishu_{fname}.md")
        with open(tmp_path, "w", encoding="utf-8") as tf:
            tf.write(md_content)

        doc_title = f"【{author}】{title[:35]}"
        print(f"匯入飛書: {doc_title} ...")
        url, _ = mgr.create_docx_from_markdown(tmp_path, doc_title=doc_title, folder_token=cfg["folder_token"])
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

        if url:
            print(f"  成功: {url}")
            results.append((author, title, url))
        else:
            print(f"  失敗: {doc_title}")
        time.sleep(1)

    print("\n" + "=" * 60)
    for a, t, u in results:
        print(f"【{a}】《{t}》\n{u}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
