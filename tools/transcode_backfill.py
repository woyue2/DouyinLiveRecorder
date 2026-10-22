#!/usr/bin/env python3
"""把已排入 B 站投稿佇列的 TS 分段補轉成低碼率 MP4，並改寫佇列路徑。

逐段轉碼功能只對新錄製生效，這個工具用來處理上線前就已進佇列的舊分段。

分兩階段，把主程序停機時間壓到幾秒：

    # 階段一（主程序照常執行，錄影不中斷）
    python3 tools/transcode_backfill.py --stage transcode

    # 階段二（必須先停主程序：它把佇列保存在記憶體並定期覆寫 JSON）
    systemctl stop ... / kill <pid>
    python3 tools/transcode_backfill.py --stage finalize
    nohup python3 -u main.py >> output.log 2>&1 &

階段一只產出 .mp4 並保留原 TS，所以短暫多佔一份空間；
階段二才刪除 TS 並改寫 bili_pending_queue.json。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

CRF = 30


def duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, timeout=120,
    )
    try:
        return float(out.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def transcode(src: Path) -> tuple[bool, str]:
    """產出 src 對應的 .mp4，保留原 TS。回傳 (是否成功, 說明)。"""
    dst = src.with_suffix(".mp4")
    tmp = str(dst) + ".part"
    src_dur = duration(str(src))
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(src),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(CRF),
        "-threads", "1",
        "-vf", "format=yuv420p",
        "-c:a", "copy",
        "-f", "mp4", tmp,
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, timeout=1800)
        if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
            raise RuntimeError("輸出為空")
        # 時長驗證：轉碼不該改變片長，差超過 2 秒視為異常
        if src_dur > 0:
            dst_dur = duration(tmp)
            if abs(dst_dur - src_dur) > 2.0:
                raise RuntimeError(f"時長不符 {src_dur:.1f}s → {dst_dur:.1f}s")
        os.replace(tmp, dst)
    except Exception as e:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False, str(e)[:160]
    return True, f"{os.path.getsize(dst) / 1024 / 1024:.1f}MB"


def collect_ts(jobs: list) -> list[Path]:
    seen, out = set(), []
    for job in jobs:
        for p in job.get("ts_paths", []):
            path = Path(p)
            if path.suffix.lower() == ".ts" and path.exists() and str(path) not in seen:
                seen.add(str(path))
                out.append(path)
    return out


def stage_transcode(paths: list[Path], dry_run: bool) -> int:
    total_before = total_after = 0
    failed = 0
    for path in paths:
        mp4 = path.with_suffix(".mp4")
        if mp4.exists() and mp4.stat().st_size > 0:
            print(f"  跳過（已有 MP4）: {mp4.name}")
            total_before += path.stat().st_size
            total_after += mp4.stat().st_size
            continue

        size_before = path.stat().st_size
        total_before += size_before
        if dry_run:
            print(f"  [試跑] {path.parent.name}/{path.name} "
                  f"({size_before / 1024 / 1024:.0f}MB)")
            total_after += size_before
            continue

        t0 = time.time()
        ok, info = transcode(path)
        dt = time.time() - t0
        if ok:
            size_after = mp4.stat().st_size
            total_after += size_after
            pct = (1 - size_after / size_before) * 100 if size_before else 0
            print(f"  OK  {path.name} → {mp4.name}  "
                  f"{size_before / 1024 / 1024:.0f}MB → {size_after / 1024 / 1024:.1f}MB "
                  f"(省 {pct:.0f}%, {dt:.0f}s)")
        else:
            total_after += size_before
            failed += 1
            print(f"  失敗 {path.name}: {info}（保留原 TS）")

    print(f"\n階段一：{total_before / 1024 ** 3:.2f}GB → {total_after / 1024 ** 3:.2f}GB"
          f"（原 TS 尚未刪除）")
    if failed:
        print(f"失敗 {failed} 段，會照原 TS 上傳")
        return 1
    return 0


def stage_finalize(queue_path: Path, dry_run: bool) -> int:
    data = json.loads(queue_path.read_text(encoding="utf-8"))
    jobs = data.get("queue", [])
    swapped = removed = 0
    changed = False

    for job in jobs:
        new_paths = []
        for p in job.get("ts_paths", []):
            src = Path(p)
            mp4 = src.with_suffix(".mp4")
            if src.suffix.lower() != ".ts" or not src.exists():
                new_paths.append(p)
                continue
            if not (mp4.exists() and mp4.stat().st_size > 0):
                new_paths.append(p)
                continue
            if dry_run:
                print(f"  [試跑] {src.name} → {mp4.name}")
                new_paths.append(str(mp4))
                changed = True
                continue

            old_marker = Path(str(src) + ".bili_uploading")
            if old_marker.exists():
                shutil.move(str(old_marker), str(mp4) + ".bili_uploading")
            try:
                os.remove(src)
                removed += 1
            except OSError as e:
                print(f"  刪除 {src.name} 失敗: {e}（仍切換到 MP4）")
            new_paths.append(str(mp4))
            swapped += 1
            changed = True
        job["ts_paths"] = new_paths

    if changed and not dry_run:
        backup = queue_path.with_suffix(f".json.bak.{int(time.time())}")
        shutil.copy2(queue_path, backup)
        queue_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  佇列已更新（備份 {backup.name}）")

    print(f"\n階段二：切換 {swapped} 段、刪除 {removed} 個 TS")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["transcode", "finalize"], default="transcode")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--queue", default="bili_pending_queue.json")
    args = ap.parse_args()

    queue_path = Path(args.queue)
    if not queue_path.exists():
        print(f"找不到佇列檔 {queue_path}")
        return 1
    data = json.loads(queue_path.read_text(encoding="utf-8"))
    jobs = data.get("queue", [])
    if not jobs:
        print("佇列為空，無需補轉")
        return 0

    paths = collect_ts(jobs)
    print(f"佇列 {len(jobs)} 場，待處理分段 {len(paths)} 個\n")
    if args.stage == "transcode":
        return stage_transcode(paths, args.dry_run)
    return stage_finalize(queue_path, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
