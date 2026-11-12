# -*- encoding: utf-8 -*-
"""精彩片段筛选：整场直播先由 LLM 挑段，再在段内挑区间，剪成短片后投稿。

為什麼要有：B 站一天最多 5 篇，初戀小娜一場 53 個分段、2.4GB，光一場就吃掉
好幾天的配額，磁碟也跟著爆；而兩小時的直播原樣傳上去本來也沒人看完。

解耦原則（刻意這樣設計，方便先在一兩個主播身上試）：
- 只吃 (session_dir, video_paths, 設定, LLM)，不碰錄製／轉寫／投稿主流程；
- 任何一步失敗都回傳 None，呼叫方原樣全量上傳，既有行為完全不受影響；
- 設定集中在 config.ini [精彩片段]，按主播名單啟用，預設關閉。

兩段式篩選：
  Stage 1 先看每段的 AI 摘要（每段 15 分鐘），挑出值得投的段，順便生成合集標題；
  Stage 2 對選中的段，用帶時間戳的轉寫選 1~N 個區間，ffmpeg 直接切出來。
時間戳來自轉寫時輸出的 <stem>.segments.json；沒有這個檔（舊場次）就整段保留。
"""
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:  # main.py 以 src.highlights 匯入
    from . import llm_client
except ImportError:  # 直接以檔案載入（測試）或獨立執行
    import llm_client

try:
    # 主程式用 loguru；標準 logging 沒有掛 handler，訊息會直接消失
    from loguru import logger
except ImportError:  # 測試環境不一定裝了 loguru
    import logging
    logger = logging.getLogger("highlights")

_HIGHLIGHT_DIR = "highlights"
_MAX_PROMPT_CHARS = 12000       # 單次呼叫塞給模型的文字上限
_MIN_PART_SECONDS = 60


@dataclass
class HighlightConfig:
    """[精彩片段] 段的設定。全部可參數化，預設關閉。"""
    enabled: bool = False
    streamers: tuple = ()
    mode: str = "clip"            # clip=段內再切區間；part=只挑整段
    min_clip_seconds: int = 45
    max_clip_seconds: int = 240
    target_minutes: int = 40
    max_parts: int = 6
    max_clips_per_part: int = 2
    delete_originals: bool = True
    model: str = ""               # 空字串＝沿用投稿的 LLM 模型
    api_key: str = ""             # 空字串＝沿用投稿的 LLM Key
    api_base: str = ""            # 空字串＝沿用投稿的 LLM 端點

    def allows(self, record_name: str) -> bool:
        if not self.enabled or not self.streamers:
            return False
        name = record_name or ""
        return any(s and s in name for s in self.streamers)


def _get(cp, key, default=""):
    try:
        return cp.get("精彩片段", key, fallback=default)
    except Exception:
        return default


def _as_int(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_bool(value, default=False):
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in ("是", "1", "true", "yes", "on")


def load_config(cp) -> HighlightConfig:
    """從 ConfigParser 讀 [精彩片段]；沒有這一段就回傳「停用」。"""
    if cp is None or not getattr(cp, "has_section", lambda _s: False)("精彩片段"):
        return HighlightConfig()

    raw_streamers = _get(cp, "启用主播", "")
    streamers = [s.strip() for s in re.split(r"[,，\s]+", raw_streamers or "") if s.strip()]
    mode = str(_get(cp, "模式", "clip")).strip().lower()
    return HighlightConfig(
        enabled=_as_bool(_get(cp, "启用", "否"), False),
        streamers=tuple(streamers),
        mode="part" if mode == "part" else "clip",
        min_clip_seconds=max(15, _as_int(_get(cp, "最短片段(秒)", 45), 45)),
        max_clip_seconds=max(30, _as_int(_get(cp, "最长片段(秒)", 240), 240)),
        target_minutes=max(5, _as_int(_get(cp, "目标时长(分钟)", 40), 40)),
        max_parts=max(1, _as_int(_get(cp, "最多保留段数", 6), 6)),
        max_clips_per_part=max(1, _as_int(_get(cp, "每段最多片段数", 2), 2)),
        delete_originals=_as_bool(_get(cp, "播出后删除原片", "是"), True),
        model=str(_get(cp, "模型", "")).strip(),
        api_key=str(_get(cp, "api_key", "")).strip(),
        api_base=str(_get(cp, "api_base", "")).strip(),
    )


# ---------------------------- 素材收集 ----------------------------

def _read_segments(video_path: Path) -> Optional[dict]:
    """讀轉寫輸出的 <stem>.segments.json；不存在或格式壞掉都當作沒有。"""
    seg_path = video_path.with_suffix(".segments.json")
    if not seg_path.exists():
        return None
    try:
        data = json.loads(seg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"時間軸檔讀取失敗 {seg_path.name}: {e}")
        return None
    segments = data.get("segments") or []
    if not segments:
        return None
    return {"duration": float(data.get("duration") or 0), "segments": segments}


def _read_summary(video_path: Path) -> tuple:
    """從同名的 .md 取 (H1 標題, 精彩看點行)，沒有就回空。"""
    md_path = video_path.with_suffix(".md")
    if not md_path.exists():
        return "", []
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return "", []
    title = ""
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if m:
        title = m.group(1).strip()
    bullets = []
    in_block = False
    for line in text.splitlines():
        if "SUMMARY_START" in line:
            in_block = True
            continue
        if "SUMMARY_END" in line:
            break
        if in_block:
            clean = line.lstrip("> ").strip().lstrip("0123456789.、) ").strip()
            if clean and len(clean) > 4 and not clean.startswith("**精彩看點") \
                    and not clean.startswith("💡"):
                bullets.append(clean)
    return title, bullets[:3]


def _probe_duration(video_path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(video_path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip() or 0)
    except (subprocess.SubprocessError, ValueError, OSError):
        return 0.0


def collect_parts(video_paths: list) -> list:
    """把影片整理成「分段」清單，附標題、摘要、時長與時間軸。"""
    parts = []
    for index, raw in enumerate(video_paths):
        path = Path(raw)
        if not path.exists():
            continue
        title, bullets = _read_summary(path)
        segments = _read_segments(path)
        duration = (segments or {}).get("duration") or _probe_duration(path)
        if duration < _MIN_PART_SECONDS:
            continue
        parts.append({
            "index": index,
            "path": str(path),
            "stem": path.stem,
            "title": title,
            "bullets": bullets,
            "duration": float(duration),
            "segments": segments,
        })
    return parts


# ---------------------------- 區間整理 ----------------------------

def normalize_ranges(raw, duration: float, min_seconds: int, max_seconds: int,
                     limit: int) -> list:
    """把模型給的區間收斂成合法、不重疊、按時間排序的清單（純函式，好測）。

    模型常犯三種錯：超出片長、短於最短時長、區間互相重疊。這裡一律就地修正，
    修不了（例如整段都超範圍）就丟掉，讓呼叫方退回整段。
    """
    clips = []
    for item in raw or []:
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError, AttributeError):
            continue
        start = max(0.0, min(start, duration))
        end = max(0.0, min(end, duration))
        if end - start < min_seconds:
            # 太短就往後補到最短時長；已經貼底就往前推
            end = min(duration, start + min_seconds)
            start = max(0.0, end - min_seconds)
        if end - start > max_seconds:
            end = start + max_seconds
        if end - start < min_seconds or end <= start:
            continue
        clips.append({"start": round(start, 2), "end": round(end, 2),
                      "title": str(item.get("title") or "").strip()[:40]})

    clips.sort(key=lambda c: c["start"])
    merged = []
    for clip in clips:
        if merged and clip["start"] < merged[-1]["end"]:
            if clip["end"] > merged[-1]["end"]:
                merged[-1]["end"] = clip["end"]
        else:
            merged.append(dict(clip))
    return merged[:limit]


def _mmss(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _timeline_text(part: dict) -> str:
    """把時間軸壓成「[mm:ss-mm:ss] 文字」，控制單次提示詞長度。"""
    segments = (part.get("segments") or {}).get("segments") or []
    lines = []
    total = 0
    for seg in segments:
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        line = f"[{_mmss(seg.get('start', 0))}-{_mmss(seg.get('end', 0))}] {text}"
        if total + len(line) > _MAX_PROMPT_CHARS:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


# ---------------------------- LLM 兩段式 ----------------------------

_STAGE1_SYSTEM = (
    "你是資深直播切片運營。下面是一場直播的分段清單，每段約 15 分鐘，附 AI 摘要。\n"
    "請挑出最值得投稿的若干段，按精彩程度排序，並生成合集標題與簡介。\n"
    "要求：\n"
    "1. 只輸出 JSON，格式 {\"title\":\"合集標題(30字內,含主播名)\","
    "\"desc\":\"合集簡介(2~4行)\",\"keep\":[段號,...]}\n"
    "2. keep 用清單裡方括號中的段號，數量不超過上限，寧缺勿濫；\n"
    "3. 不要選純開場寒暄、長時間無人聲、複讀或明顯推流卡頓的段；\n"
    "4. 標題要像人寫的，不要出現「第X段」「直播精華」「網址」。"
)

_STAGE2_SYSTEM = (
    "你是資深直播切片剪輯師。下面是某 15 分鐘片段的帶時間戳轉寫，時間戳格式 [mm:ss-mm:ss]。\n"
    "請選出最精彩的連續區間，用於剪成短片投稿。\n"
    "要求：\n"
    "1. 只輸出 JSON，格式 {\"clips\":[{\"start\":秒,\"end\":秒,\"title\":\"這段看點(15字內)\"}]}\n"
    "2. 每段時長必須在 {min_s}~{max_s} 秒之間，最多 {limit} 段，區間不得重疊；\n"
    "3. start/end 用距離開頭多少秒（數字），必須落在 0~{duration} 秒內；\n"
    "4. 優先選有完整笑點、衝突、反轉或名場面的區間，不要選開場報到、長時間空白。"
)


def pick_parts(parts: list, cfg: HighlightConfig, api_key: str,
               api_base: Optional[str], model: str) -> Optional[dict]:
    """Stage 1：挑段並生成合集標題。失敗回 None（呼叫方全量上傳）。"""
    if not parts:
        return None
    lines = []
    for p in parts:
        head = p.get("title") or p["stem"]
        bullets = "；".join(p.get("bullets") or [])
        lines.append(f"[{p['index']}] {head}" + (f"\n    - {bullets}" if bullets else ""))
    user = (f"共 {len(parts)} 段，最多保留 {cfg.max_parts} 段，"
            f"目標總時長約 {cfg.target_minutes} 分鐘。\n\n" + "\n".join(lines))
    reply = llm_client.chat(_STAGE1_SYSTEM, user[: _MAX_PROMPT_CHARS],
                            api_key=api_key, api_base=api_base, model=model,
                            max_tokens=800, temperature=0.4)
    data = llm_client.extract_json(reply or "", expect="object")
    if not isinstance(data, dict):
        logger.warning("精彩片段 Stage1 解析失敗，退回全量上傳")
        return None
    valid = {p["index"] for p in parts}
    keep = [k for k in (data.get("keep") or []) if isinstance(k, int) and k in valid]
    if not keep:
        logger.warning("精彩片段 Stage1 沒有給出有效段號，退回全量上傳")
        return None
    return {
        "title": str(data.get("title") or "").strip()[:80],
        "desc": str(data.get("desc") or "").strip(),
        "keep": keep[: cfg.max_parts],
    }


def pick_ranges(part: dict, cfg: HighlightConfig, api_key: str,
                api_base: Optional[str], model: str) -> list:
    """Stage 2：在單一段內挑區間。失敗回空清單（該段整段保留）。"""
    timeline = _timeline_text(part)
    if not timeline:
        return []
    system = (_STAGE2_SYSTEM.replace("{min_s}", str(cfg.min_clip_seconds))
              .replace("{max_s}", str(cfg.max_clip_seconds))
              .replace("{limit}", str(cfg.max_clips_per_part))
              .replace("{duration}", str(int(part["duration"]))))
    reply = llm_client.chat(system, timeline, api_key=api_key, api_base=api_base,
                            model=model, max_tokens=600, temperature=0.3)
    data = llm_client.extract_json(reply or "", expect="object")
    if not isinstance(data, dict):
        logger.warning(f"精彩片段 Stage2 解析失敗，整段保留: {part['stem']}")
        return []
    return normalize_ranges(data.get("clips"), part["duration"],
                            cfg.min_clip_seconds, cfg.max_clip_seconds,
                            cfg.max_clips_per_part)


# ---------------------------- 剪切與組裝 ----------------------------

def cut_clip(src: str, start: float, end: float, out_path: str) -> bool:
    """用 ffmpeg 無損剪切；-ss 放前面是關鍵幀快速定位，容許幾秒誤差。"""
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", src,
        "-c", "copy", "-avoid_negative_ts", "make_zero",
        out_path,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if res.returncode != 0 or not os.path.exists(out_path):
            logger.warning(f"剪切失敗 {Path(src).name} {start}-{end}: {res.stderr[:200]}")
            return False
        return os.path.getsize(out_path) > 0
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning(f"剪切異常 {Path(src).name}: {e}")
        return False


def maybe_apply(session_dir: str, video_paths: list, record_name: str,
                cfg: HighlightConfig, api_key: Optional[str] = None,
                api_base: Optional[str] = None, model: str = "deepseek-chat",
                ) -> Optional[dict]:
    """主入口：回傳 {"ts_paths": [...], "meta": {...}, "originals": [...]}。

    任何一步不滿足都回傳 None，呼叫方按原樣全量上傳。
    """
    if not cfg.allows(record_name):
        return None
    # [精彩片段] 可以自帶憑證：B 站投稿那段不一定配了 Key
    api_key = cfg.api_key or api_key
    api_base = cfg.api_base or api_base
    model = cfg.model or model
    if not api_key:
        logger.info("精彩片段：未配置 LLM Key，跳過篩選")
        return None

    try:
        parts = collect_parts(video_paths)
        if len(parts) < 2:
            logger.info("精彩片段：分段不足 2 段，不做篩選")
            return None

        stage1 = pick_parts(parts, cfg, api_key, api_base, cfg.model or model)
        if not stage1:
            return None

        by_index = {p["index"]: p for p in parts}
        selected = [by_index[i] for i in stage1["keep"] if i in by_index]
        out_dir = Path(session_dir) / _HIGHLIGHT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)

        clips, clip_titles, part_labels = [], [], []
        for order, part in enumerate(selected, start=1):
            ranges = []
            if cfg.mode == "clip" and part.get("segments"):
                ranges = pick_ranges(part, cfg, api_key, api_base, cfg.model or model)

            if ranges:
                made = []
                for n, clip in enumerate(ranges, start=1):
                    out = out_dir / f"{part['stem']}_h{n:02d}.mp4"
                    if cut_clip(part["path"], clip["start"], clip["end"], str(out)):
                        made.append((str(out), clip.get("title") or f"片段{n}"))
                if not made:
                    made = [(part["path"], part.get("title") or "")]
            else:
                made = [(part["path"], part.get("title") or "")]

            for path, title in made:
                clips.append(path)
                clip_titles.append(title)
                part_labels.append(order)

        if not clips:
            return None
        logger.info(
            f"精彩片段：{len(parts)} 段 → 保留 {len(selected)} 段 / {len(clips)} 个片段 "
            f"({record_name})"
        )
        return {
            "ts_paths": clips,
            "meta": build_meta(stage1, clips, clip_titles, part_labels, record_name),
            "originals": [p["path"] for p in parts],
            "selected_parts": [p["path"] for p in selected],
        }
    except Exception as e:  # 篩選壞了不能拖垮投稿
        logger.warning(f"精彩片段篩選異常，退回全量上傳: {type(e).__name__}: {e}")
        return None


def build_meta(stage1: dict, clips: list, clip_titles: list, part_labels: list,
               record_name: str) -> dict:
    """組出與 extract_session_bili_metadata 相同的結構，後續拆稿/上傳不用改。"""
    clean_name = str(record_name).split(" ", maxsplit=1)[-1].strip()
    parts, descs = [], []
    for i, (title, src) in enumerate(zip(clip_titles, part_labels), start=1):
        label = f"【P{i}】"
        seed = title.strip()
        parts.append({
            "label": label,
            "lines": [seed] if seed else [],
            "title": (f"P{i} {seed}" if seed else f"P{i}")[:80],
        })
        descs.append(f"{label}\n{seed}" if seed else f"{label}\n精彩片段 {i}")
    overall = stage1.get("title") or f"{clean_name} 直播精彩片段"
    desc = stage1.get("desc") or "\n\n".join(descs)
    if len(desc) > 2000:
        desc = desc[:1997] + "..."
    return {
        "title": overall[:80],
        "desc": desc,
        "tags": f"{clean_name},直播切片,名场面",
        "parts": parts,
        "part_descs": [seed for seed in clip_titles],
    }


def delete_originals(paths: list, log=logger) -> int:
    """篩選成功且投完後刪掉原片（連同同名 mp3/md 旁檔），避免磁碟只增不減。"""
    removed = 0
    for raw in paths or []:
        for candidate in (raw, str(Path(raw).with_suffix(".mp3"))):
            try:
                if os.path.exists(candidate):
                    os.remove(candidate)
                    removed += 1
            except OSError as e:
                log.warning(f"刪除原片失敗 {candidate}: {e}")
    return removed
