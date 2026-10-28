# -*- encoding: utf-8 -*-
"""為「已收播但還沒生成飛書時間軸」的場次補生成文檔。

正常情況下時間軸在收播時就生成；這個工具用來補跑歷史場次，
前提是場次目錄裡的 .md（轉寫結果）還在——它是看點的來源。

    python3 tools/backfill_timeline.py --session "downloads/抖音直播/黃財神/2026-10-23_21-57" \
        --name "序号5 黃財神" --dry-run
    python3 tools/backfill_timeline.py --session "..." --name "..."
"""
import argparse
import configparser
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from bili_uploader import (  # noqa: E402
    compute_part_start_times,
    extract_session_bili_metadata,
    list_session_videos,
    session_date_label,
    build_parts_timeline_markdown,
)


def load_cfg():
    cp = configparser.RawConfigParser()
    for p in (_ROOT / "config" / "config.ini", _ROOT / "config" / "local_secrets.ini"):
        if p.exists():
            cp.read(p, encoding="utf-8-sig")
    return cp


def get(cp, section, key, default=""):
    if cp.has_option(section, key):
        return cp.get(section, key).strip()
    return default


def clean_name_of(record_name: str) -> str:
    name = record_name.split(" ", maxsplit=1)[-1].strip()
    return name or record_name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True, help="場次目錄")
    ap.add_argument("--name", required=True, help="主播名（如「序号5 黃財神」）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    session_dir = str(Path(args.session).resolve())
    videos = [str(f) for f in list_session_videos(session_dir)]
    if not videos:
        print(f"沒有可用的影片分段: {session_dir}")
        return 1
    md_count = len(list(Path(session_dir).glob("*.md")))
    print(f"素材: {len(videos)} 段影片, {md_count} 個 md")

    cp = load_cfg()
    clean_name = clean_name_of(args.name)
    fallback = int(float(get(cp, "录制设置", "视频分段时间(秒)", "900") or 900))

    meta = extract_session_bili_metadata(
        session_dir=session_dir,
        record_name=args.name,
        streamer_type="娱乐搞笑",
        default_tags=get(cp, "B站投稿", "默认标签", ""),
        llm_api_key=get(cp, "B站投稿", "大模型api_key") or None,
        llm_api_base=get(cp, "B站投稿", "大模型api_base") or None,
        llm_model=get(cp, "B站投稿", "大模型model") or "deepseek-chat",
        ts_paths=videos,
    )
    parts = meta.get("parts") or []
    if not parts:
        print("大模型沒有產生分P看點，放棄")
        return 1
    print(f"分P看點: {len(parts)} 條")

    starts = compute_part_start_times(videos, fallback_seconds=fallback)
    date_label = session_date_label(session_dir)
    md = build_parts_timeline_markdown(
        parts, record_name=args.name, upload_date=date_label, start_times=starts,
    )
    print(f"Markdown {len(md)} 字，前 3 行:")
    for line in md.splitlines()[:3]:
        print("   ", line[:60])
    if args.dry_run:
        return 0

    doc_title = f"{date_label}【{clean_name}】直播时间轴（补生成）"
    tmp = f"/tmp/backfill_timeline_{os.getpid()}.md"
    Path(tmp).write_text(md, encoding="utf-8")
    try:
        from feishu_uploader import FeishuDocManager
        mgr = FeishuDocManager(
            get(cp, "飞书文档配置", "飞书app_id"),
            get(cp, "飞书文档配置", "飞书app_secret"),
            folder_token=get(cp, "飞书文档配置", "飞书文件夹_token"),
            wiki_space_id=get(cp, "飞书文档配置", "飞书知识库space_id"),
            wiki_root_node=get(cp, "B站投稿", "直播时间轴节点"),
            wiki_domain=get(cp, "飞书文档配置", "飞书域名") or "my.feishu.cn",
        )
        url, token = mgr.create_article_docx(tmp, author=clean_name, doc_title=doc_title)
        print("文檔:", url)
        print("doc_token:", token)
        # 登記起來，之後投稿成功才能把 BV 號補進這篇（與 main.py 用同一個檔案）
        if url and token:
            import json
            map_path = _ROOT / "downloads" / "bili_timeline_docs.json"
            data = {}
            if map_path.exists():
                try:
                    data = json.loads(map_path.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            data[session_dir] = {"doc_token": token, "url": url,
                                 "at": time.time(), "meta": meta, "published": []}
            map_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                encoding="utf-8")
            print("已登記，投稿成功後會自動補 BV 號")
    finally:
        os.remove(tmp)
    return 0 if 'url' in dir() and url else 1


if __name__ == "__main__":
    sys.exit(main())
