# -*- coding: utf-8 -*-
"""Bilibili 自动投稿与元数据解析模块

负责：
1. 从转写生成的 .md 文件中提取适合 B 站娱乐/知识直播的有流量标题、精彩看点简介、标签；
2. 过滤原直播间链接与老套标签（如【直播精华】）；
3. 调用 biliup 进行视频投稿；
4. 投稿成功后根据策略清理本地 TS 文件，保留 MD 文件供百度云文字备份。
"""

import os
import re
import json
import subprocess
import shutil
import logging
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def extract_bili_metadata(
    md_path: str,
    record_name: str,
    streamer_type: str = "娱乐搞笑",
    default_tags: Optional[str] = None,
    llm_api_key: Optional[str] = None,
    llm_api_base: Optional[str] = None,
    llm_model: str = "deepseek-chat"
) -> Dict[str, str]:
    """从转写 .md 文件中解析出适合 B 站投稿的标题、简介与标签。

    若提供了 llm_api_key，会优先调用大语言模型为本段内容撰写爆款标题与看点；
    未配置或调用失败时，自动回退到基于规则的高能看点提炼。

    Args:
        md_path: .md 文件绝对或相对路径
        record_name: 主播名或房间标识（例如：合家欢乐乐乐）
        streamer_type: 主播直播类型（娱乐搞笑 / 知识干货 / 聊天日常）
        default_tags: 默认追加标签
        llm_api_key: 可选的大模型 API Key
        llm_api_base: 可选的 API 基础地址（如 https://api.deepseek.com）
        llm_model: 模型名称

    Returns:
        dict 包含 'title', 'desc', 'tags'
    """
    clean_streamer_name = record_name.split(" ", maxsplit=1)[-1].strip()
    # 去除可能的前缀，如 "主播: "
    clean_streamer_name = re.sub(r'^(主播|anchor)[:：]\s*', '', clean_streamer_name).strip()

    title = ""
    tags = ""
    desc_content = ""

    md_content = ""
    # 若原路径不存在，尝试从 ./converted/ 备份暂存目录查找
    if not os.path.exists(md_path):
        p = Path(md_path)
        converted_candidate = p.parent / "converted" / p.name
        if converted_candidate.exists():
            md_path = str(converted_candidate)

    if os.path.exists(md_path):
        try:
            md_content = Path(md_path).read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"读取 MD 文件失败: {md_path}, 错误: {e}")

    # 0. 若配置了 LLM，优先尝试由 LLM 生成网感标题与简介
    active_api_key = llm_api_key or os.getenv("BILI_LLM_API_KEY")
    if active_api_key and md_content:
        llm_meta = generate_bili_meta_with_llm(
            text_content=md_content,
            record_name=record_name,
            streamer_type=streamer_type,
            api_key=active_api_key,
            api_base=llm_api_base,
            model=llm_model
        )
        if llm_meta:
            return llm_meta

    # 1. 尝试从专用的 <!--BILI_META_START--> 标记中提取
    bili_meta_match = re.search(
        r'<!--BILI_META_START-->\s*(.*?)\s*<!--BILI_META_END-->',
        md_content,
        re.DOTALL
    )
    if bili_meta_match:
        meta_block = bili_meta_match.group(1).strip()
        title_match = re.search(r'^(?:TITLE|标题)[:：]\s*(.+)$', meta_block, re.MULTILINE | re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()
        tags_match = re.search(r'^(?:TAGS|标签)[:：]\s*(.+)$', meta_block, re.MULTILINE | re.IGNORECASE)
        if tags_match:
            tags = tags_match.group(1).strip()

    # 2. 提取摘要与精彩看点 <!--SUMMARY_START-->
    summary_match = re.search(
        r'<!--SUMMARY_START-->\s*(.*?)\s*<!--SUMMARY_END-->',
        md_content,
        re.DOTALL
    )
    if summary_match:
        raw_summary = summary_match.group(1).strip()
        clean_lines = []
        for line in raw_summary.splitlines():
            # 清理引用符号
            l = line.lstrip('> ').strip()
            # 过滤任何外部链接与原直播间链接
            l = re.sub(r'https?://\S+', '', l).strip()
            # 过滤【直播精华】、录播等字眼
            l = re.sub(r'【直播[精核心]+】', '', l).strip()
            if l:
                clean_lines.append(l)
        desc_content = "\n".join(clean_lines).strip()

    # 3. 如果未显式提供 TITLE，则从摘要/看点首行智能提炼有流量的标题
    if not title:
        candidate_title = ""
        if desc_content:
            for line in desc_content.splitlines():
                # 跳过纯标题/导语行如 "精彩看点："、"本段精彩看点："、"### 高能笑点"
                if re.match(r'^#*\s*(本段|今日|本次)?(精彩|高能|核心|爆笑)?(看点|笑点|内容|提炼|摘要|干货)[:：]?$', line.strip()):
                    continue
                # 清洗序号或列表符号
                clean_line = re.sub(r'^[-*•\d+.\s、]+', '', line).strip()
                # 去除时间戳标记如 [02:15] 或 02:15
                clean_line = re.sub(r'^\[?\d{1,2}:\d{2}\]?\s*', '', clean_line).strip()
                if len(clean_line) >= 4:
                    candidate_title = clean_line
                    break

        if candidate_title:
            # 娱乐搞笑类标题网感包装
            short_name = clean_streamer_name[-2:] if len(clean_streamer_name) >= 4 else clean_streamer_name
            # 如果候选标题开头已经有主播全名或常用简称，避免机械重复
            if candidate_title.startswith(clean_streamer_name):
                title = candidate_title
            elif candidate_title.startswith(short_name):
                # 例如 "乐乐模仿..." 优化为 "合家欢乐乐乐：模仿..." 或保留全名
                rest = candidate_title[len(short_name):].lstrip('：:，, ')
                title = f"{clean_streamer_name}：{rest}" if rest else candidate_title
            else:
                title = f"{clean_streamer_name}：{candidate_title}"
        else:
            # 回退默认标题：文件名时间或简单标识
            file_stem = Path(md_path).stem
            title = f"{clean_streamer_name} 直播高能名场面 ({file_stem[-3:] if file_stem[-3:].isdigit() else '片段'})"

    # 严格保证 B 站标题约束：
    # 1. 不含【直播精华】
    title = re.sub(r'【直播[精核心]+】', '', title).strip()
    # 2. 不超过 80 个字符限制
    if len(title) > 80:
        title = title[:77] + "..."

    # 4. 标签处理
    tag_list = []
    if tags:
        tag_list.extend([t.strip()[:20] for t in re.split(r'[,，\s]+', tags) if t.strip()])

    # 确保主播名在标签首位
    if clean_streamer_name:
        if clean_streamer_name in tag_list:
            tag_list.remove(clean_streamer_name)
        tag_list.insert(0, clean_streamer_name[:20])

    # 娱乐主播特定配套标签
    type_tags = {
        "娱乐搞笑": ["搞笑", "名场面", "主播日常", "搞笑日常", "高能"],
        "知识干货": ["知识", "干货", "商业思维", "认知提升"],
        "聊天日常": ["日常", "闲聊", "互动", "治愈"],
    }.get(streamer_type, ["娱乐", "搞笑"])

    for t in type_tags:
        t_clean = t[:20]
        if t_clean not in tag_list:
            tag_list.append(t_clean)

    if default_tags:
        for t in re.split(r'[,，\s]+', default_tags):
            t_clean = t.strip()[:20]
            if t_clean and t_clean not in tag_list:
                tag_list.append(t_clean)

    # B 站单个视频最多 12 个标签，且单标签不超过 20 字
    final_tags = ",".join(tag_list[:12])

    # 5. 简介兜底（确保不带外部直播间链接，且限制长度）
    if not desc_content:
        desc_content = f"{clean_streamer_name} 直播精彩片段分享，喜欢欢迎关注点赞！"
    elif len(desc_content) > 1000:
        desc_content = desc_content[:997] + "..."

    return {
        "title": title,
        "desc": desc_content,
        "tags": final_tags,
    }


def extract_session_bili_metadata(
    session_dir: str,
    record_name: str,
    streamer_type: str = "娱乐搞笑",
    default_tags: Optional[str] = None,
    llm_api_key: Optional[str] = None,
    llm_api_base: Optional[str] = None,
    llm_model: str = "deepseek-chat"
) -> Dict[str, str]:
    """汇总整场直播各个分段的 .md 文件，生成适合多 P 合集投稿的总体爆款标题与分 P 时间戳目录。"""
    p = Path(session_dir)
    md_files = sorted(p.glob("*.md"))
    unique_mds = []
    seen_stems = set()
    for f in md_files:
        stem = f.stem.replace("-敏感", "")
        if stem not in seen_stems:
            seen_stems.add(stem)
            unique_mds.append(f)

    if not unique_mds:
        return extract_bili_metadata(
            md_path=str(p / "dummy.md"),
            record_name=record_name,
            streamer_type=streamer_type,
            default_tags=default_tags,
            llm_api_key=llm_api_key,
            llm_api_base=llm_api_base,
            llm_model=llm_model
        )

    part_descs = []
    combined_texts = []
    parts_index = []
    for idx, md_path in enumerate(unique_mds):
        meta = extract_bili_metadata(
            md_path=str(md_path),
            record_name=record_name,
            streamer_type=streamer_type,
            default_tags=default_tags,
            llm_api_key=llm_api_key or os.getenv("BILI_LLM_API_KEY"),
            llm_api_base=llm_api_base,
            llm_model=llm_model
        )
        p_label = f"【P{idx+1}】"
        desc = meta["desc"]
        part_descs.append(f"{p_label}\n{desc}")
        parts_index.append({
            "label": p_label,
            "lines": _extract_index_lines(desc),
        })
        try:
            combined_texts.append(md_path.read_text(encoding="utf-8")[:1000])
        except Exception:
            pass

    full_desc = "\n\n".join(part_descs).strip()
    # B 站简介上限 2000 字；超出部分由置顶评论承载
    if len(full_desc) > 2000:
        full_desc = full_desc[:1997] + "..."

    clean_name = record_name.split(" ", maxsplit=1)[-1].strip()
    clean_name = re.sub(r'^(主播|anchor)[:：]\s*', '', clean_name).strip()

    overall_title = ""
    active_api_key = llm_api_key or os.getenv("BILI_LLM_API_KEY")
    if active_api_key and combined_texts:
        llm_meta = generate_bili_meta_with_llm(
            text_content="\n".join(combined_texts),
            record_name=record_name,
            streamer_type=streamer_type,
            api_key=active_api_key,
            api_base=llm_api_base,
            model=llm_model
        )
        if llm_meta and llm_meta.get("title"):
            overall_title = llm_meta["title"]

    if not overall_title:
        overall_title = f"{clean_name} 直播高能精彩合集"

    return {
        "title": overall_title[:80],
        "desc": full_desc,
        "tags": f"{clean_name},搞笑,名场面,主播日常,直播录像,合集",
        "parts": parts_index,
    }


def _extract_index_lines(desc: str, limit: int = 4) -> list:
    """从分段简介中抽取用于置顶评论索引的精简行（优先带时间戳的行）。"""
    timed, plain = [], []
    for raw in desc.splitlines():
        line = raw.strip().lstrip("*# ").strip()
        if not line:
            continue
        # 跳过纯小标题行（如「精彩看点」「核心干货」）
        if re.match(r'^[🎯💡✅❌📌\s]*(精彩看点|核心干货|高能看点|看点|干货)[:：]?$', line):
            continue
        line = line.replace('**', '')
        if re.match(r'^\d{1,2}:\d{2}', line):
            timed.append(line)
        else:
            plain.append(line)
    picked = timed[:limit]
    if len(picked) < limit:
        picked += plain[:limit - len(picked)]
    return picked


def upload_video_biliup(
    video_path: str | list[str],
    title: str,
    desc: str,
    tags: str,
    tid: int = 138,
    cookie_path: str = "cookies.json",
    line: Optional[str] = None,
    copyright_type: int = 1,
    source: str = "",
    biliup_bin: str = "biliup",
    is_only_self: bool = False
) -> Tuple[bool, str]:
    """调用 biliup 命令上传视频到 B 站（支持单个视频或整场多 P 合集批量上传）。

    Args:
        video_path: 单个视频路径，或多个分段视频路径列表 (多P合集)
        title: 视频标题
        desc: 视频简介
        tags: 视频标签，英文逗号分隔
        tid: B 站分区 ID（默认 138: 搞笑）
        cookie_path: cookies.json 路径
        line: 上传线路 (如 bldsa, kodo, tx, cos)
        copyright_type: 1 自制，2 转载
        source: 转载来源描述（不放 URL）
        biliup_bin: biliup 可执行文件路径（默认 'biliup'）
        is_only_self: 是否仅自己可见（私密投稿，默认 False 公开）

    Returns:
        (是否成功, 日志/错误信息)
    """
    video_paths = [video_path] if isinstance(video_path, str) else list(video_path)
    valid_paths = [str(p) for p in video_paths if os.path.exists(str(p))]

    if not valid_paths:
        return False, f"视频文件不存在: {video_paths}"

    if not os.path.exists(cookie_path):
        return False, f"B 站 Cookie 文件不存在: {cookie_path}，请先执行 biliup login 登录"

    # 智能定位 biliup 可执行文件路径
    resolved_bin = str(biliup_bin or "biliup")
    if not shutil.which(resolved_bin):
        candidates = [
            os.path.expanduser("~/.local/bin/biliup"),
            "/home/ubuntu/.local/bin/biliup",
        ]
        for c in candidates:
            if os.path.exists(c):
                resolved_bin = c
                break

    cmd = [
        resolved_bin,
        "-u", str(cookie_path),
        "upload",
    ] + valid_paths + [
        "--title", title,
        "--desc", desc,
        "--tag", tags,
        "--tid", str(tid),
        "--copyright", str(copyright_type),
    ]

    if is_only_self:
        cmd.extend(["--is-only-self", "1"])

    if copyright_type == 2 and source:
        cmd.extend(["--source", source])

    if line:
        cmd.extend(["--line", line])

    logger.info(f"开始执行 B 站上传: 标题={title}, 视频数={len(valid_paths)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=7200,
            encoding="utf-8",
            errors="replace"
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        if result.returncode == 0:
            logger.info(f"B 站上传成功: {title}")
            return True, output
        else:
            is_rate_limited = any(k in output for k in ("21566", "过于频繁", "601"))
            if is_rate_limited:
                logger.warning("B 站投稿触发风控限制 (code 21566/601)")
                return False, f"RATE_LIMITED_21566: {output.strip()[-300:]}"
            logger.error(f"B 站上传失败 (退出码 {result.returncode}): {output[:500]}")
            return False, output
    except subprocess.TimeoutExpired:
        return False, "biliup 上传超时 (超过 2 小时)"
    except Exception as e:
        return False, f"biliup 执行异常: {type(e).__name__}: {e}"


def handle_post_upload_cleanup(video_path: str | list[str], policy: str = "仅百度云上传md(删除ts)") -> None:
    """根据 Setting 策略处理上传完成后的本地视频与占位文件。

    策略选项:
    - "仅百度云上传md(删除ts)": 删除本地 TS 视频与 .bili_uploading 占位，保留 MD 文件
    - "百度云继续上传ts": 仅删除 .bili_uploading 占位，保留 TS 供百度云脚本搬运
    - "保留本地": 不删除 TS，移除占位
    """
    paths = [video_path] if isinstance(video_path, str) else list(video_path)
    for vp in paths:
        placeholder = vp + ".bili_uploading"
        try:
            if os.path.exists(placeholder):
                os.remove(placeholder)
        except OSError as e:
            logger.warning(f"移除 B 站占位符失败 {placeholder}: {e}")

        if policy == "仅百度云上传md(删除ts)":
            try:
                if os.path.exists(vp):
                    os.remove(vp)
                    logger.info(f"[B站策略执行] 视频已上传B站，本地TS已按配置删除(百度云不上传): {vp}")
            except OSError as e:
                logger.error(f"删除已上传视频失败 {vp}: {e}")


def generate_bili_meta_with_llm(
    text_content: str,
    record_name: str,
    streamer_type: str = "娱乐搞笑",
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    model: str = "deepseek-chat"
) -> Optional[Dict[str, str]]:
    """调用大语言模型（兼容 MiniMax / DeepSeek / OpenAI 接口）生成爆款 B 站标题、高能简介与标签。

    若未配置 API Key 或请求失败，返回 None，调用方将自动回退到规则提取。
    """
    if not api_key or not text_content.strip():
        return None

    clean_streamer_name = record_name.split(" ", maxsplit=1)[-1].strip()
    clean_streamer_name = re.sub(r'^(主播|anchor)[:：]\s*', '', clean_streamer_name).strip()

    # 预处理：清洗由于网络卡顿、推流掉包或 ASR 幻觉造成的单句连续机械重复行
    cleaned_lines = []
    last_clean = ""
    repeat_count = 0
    for raw_line in text_content.splitlines():
        text_part = re.sub(r'^\[?\d{1,2}:\d{2}\]?\s*', '', raw_line).strip()
        if text_part == last_clean and text_part:
            repeat_count += 1
            if repeat_count < 2:  # 最多保留 1 次重复
                cleaned_lines.append(raw_line)
        else:
            last_clean = text_part
            repeat_count = 0
            cleaned_lines.append(raw_line)
    filtered_content = "\n".join(cleaned_lines)

    prompt_map = {
        "娱乐搞笑": (
            f"你是一位B站与抖音百万爆款娱乐切片运营专家。\n"
            f"请根据主播【{clean_streamer_name}】的直播文本，提炼出最抓人眼球的名场面与笑点。\n"
            f"【要求】：\n"
            f"1. 严禁出现任何网址或直播间链接！\n"
            f"2. 严禁出现【直播精华】、录播、第X段等死板字眼！\n"
            f"3. 严禁将「网络卡顿」、「复读机循环」、「单句机械重复」等推流故障或语音识别幻觉当成看点！必须选择有真实情节、段子、聊天互动或PK对局的名场面。\n"
            f"4. 严禁使用「干货」、「深度解析」、「核心观点」、「认知提升」、「经验分享」等沉闷知识型字眼！即使主播在聊日常或走心话题，也必须以幽默、搞笑、名场面、吃瓜等娱乐网感语气撰写。\n"
            f"5. 标题：必须极具网感、爆笑或反转名场面，包含主播名或简称，30字内吸引点击。\n"
            f"6. 简介：提炼2~4个本段高能笑点/名场面（每行一个）。若文本中包含时间戳信息，每行开头必须附带时间戳（格式如 02:15、08:30），B站观众点击时间戳可直接跳转进度条！\n"
            f"7. 标签：以英文逗号分隔5~8个热门标签，首个标签为主播名。\n"
            f"必须以合法JSON格式输出，字段包含: title, desc, tags。请确保JSON字符串中的换行用\\n表示，不要使用未转义的特殊字符。"
        ),
        "知识干货": (
            f"你是一位资深B站商业与知识内容高级主编。\n"
            f"请根据知识主播【{clean_streamer_name}】的直播文本，提炼出最有价值的深度认知、商业底层逻辑与破局方法论。\n"
            f"【要求】：\n"
            f"1. 严禁出现任何网址或直播间链接！\n"
            f"2. 严禁出现【直播精华】、录播、第X段等死板字眼！\n"
            f"3. 严禁将网络卡顿或单句机械重复当成干货！\n"
            f"4. 标题：突出最具颠覆性认知或核心方法论，必须包含主播名（如【{clean_streamer_name}】），30字内极具启发感与点击欲望。\n"
            f"5. 简介：条理清晰罗列3~5个核心干货观点。若文本包含时间戳，每行开头必须附带时间戳（如 02:15、08:30），B站观众点击时间戳可直接跳转进度条！\n"
            f"6. 标签：以英文逗号分隔5~8个高价值知识标签，首个标签为主播名（如 {clean_streamer_name},商业思维,认知升级,个人成长）。\n"
            f"必须以合法JSON格式输出，字段包含: title, desc, tags。请确保JSON字符串中的换行用\\n表示，不要使用未转义的特殊字符。"
        ),
    }

    system_prompt = prompt_map.get(streamer_type, prompt_map["娱乐搞笑"])
    user_content = filtered_content[:4000]  # 截取清洗后的前 4000 字符用于分析

    base_url = (api_base or "https://api.deepseek.com").rstrip("/")
    is_anthropic_api = "/anthropic" in base_url or "api.anthropic.com" in base_url

    import json
    import urllib.request

    if is_anthropic_api:
        # MiniMax Anthropic 兼容端点或原生 Anthropic 协议
        url = base_url + "/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01"
        }
        payload = {
            "model": model,
            "max_tokens": 1024,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": f"直播内容文本如下：\n{user_content}"}
            ]
        }
    else:
        # 标准 OpenAI / DeepSeek / MiniMax v1 兼容端点
        url = base_url + ("/v1/chat/completions" if not base_url.endswith("/v1") else "/chat/completions")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"直播内容文本如下：\n{user_content}"}
            ],
            "temperature": 0.7
        }

    last_error = None
    for attempt in range(3):  # JSON 解析失败时最多重试 3 次
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
            if is_anthropic_api:
                content = resp_data["content"][0]["text"]
            else:
                content = resp_data["choices"][0]["message"]["content"]

            # 清理可能的 markdown 代码块标记 ```json ... ```
            content_cleaned = re.sub(r'^```(?:json)?\s*', '', content.strip(), flags=re.IGNORECASE)
            content_cleaned = re.sub(r'\s*```$', '', content_cleaned.strip())
            # 提取首个 JSON 对象并用 strict=False 解析（允许字符串内存在原始换行）
            json_match = re.search(r'\{[\s\S]*\}', content_cleaned)
            json_str = json_match.group(0) if json_match else content_cleaned
            try:
                result = json.loads(json_str, strict=False)
            except Exception:
                # 容错：把 JSON 字符串内部未转义的裸换行转义为 \n
                escaped = re.sub(r'(?<!\\)"(\s*)\n(\s*)"', r'"\n"', json_str)
                escaped = re.sub(r'[\x00-\x09\x0b-\x1f]', '', escaped)
                result = json.loads(escaped, strict=False)

            title = str(result.get("title", "")).strip()
            desc = str(result.get("desc", "")).strip()

            raw_tags = result.get("tags", "")
            if isinstance(raw_tags, list):
                tag_list = [str(t).strip()[:20] for t in raw_tags if str(t).strip()]
                tags = ",".join(tag_list)
            else:
                tag_list = [t.strip()[:20] for t in str(raw_tags).split(",") if t.strip()]
                tags = ",".join(tag_list)

            # 净化：剥离 markdown 加粗标记、去除链接与【直播精华】等死板字眼
            title = re.sub(r'【直播[精核心]+】', '', title).replace('**', '').strip()
            desc = desc.replace('**', '')
            desc = re.sub(r'https?://\S+', '', desc)
            desc = re.sub(r'^[💡🎯✅❌]\s*', '', desc, flags=re.MULTILINE).strip()
            if len(desc) > 1000:
                desc = desc[:997] + "..."

            if title:
                logger.info(f"LLM 成功生成 B 站元数据: 标题={title}")
                return {
                    "title": title[:80],
                    "desc": desc,
                    "tags": tags or f"{clean_streamer_name},搞笑,名场面",
                }
            last_error = "LLM 返回内容缺少 title 字段"
        except Exception as e:
            last_error = str(e)
            logger.warning(f"LLM 调用第 {attempt + 1} 次失败: {e}")
    logger.warning(f"调用 LLM 生成 B 站元数据失败（重试 3 次）: {last_error}，将回退到规则提取")
    return None


# ------------------------- B 站评论接口 -------------------------

_BILI_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
# B 站评论单条字数上限（超过会被接口拒绝）
BILI_COMMENT_MAX_LEN = 1000


def _load_cookie_header(cookie_path: str) -> Tuple[str, Dict[str, str]]:
    """读取 biliup 保存的 cookies.json，返回 (Cookie 请求头, cookie 字典)。"""
    with open(cookie_path, encoding="utf-8") as f:
        data = json.load(f)
    cookies = data.get("cookie_info", {}).get("cookies", {})
    if isinstance(cookies, dict):
        pairs = list(cookies.items())
    else:
        pairs = [(c["name"], c["value"]) for c in cookies]
    header = "; ".join(f"{k}={v}" for k, v in pairs)
    return header, dict(pairs)


def _bili_api(url: str, cookie_header: str, data: Optional[dict] = None,
              timeout: int = 20) -> dict:
    """调用 B 站开放接口（带 cookie 认证）。"""
    if data is not None:
        req = urllib.request.Request(
            url,
            data=urllib.parse.urlencode(data).encode("utf-8"),
            method="POST",
            headers={
                "User-Agent": _BILI_UA,
                "Cookie": cookie_header,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://www.bilibili.com/",
            },
        )
    else:
        req = urllib.request.Request(
            url, headers={"User-Agent": _BILI_UA, "Cookie": cookie_header})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_aid_from_bvid(bvid: str, cookie_path: str = "cookies.json") -> Optional[int]:
    """由 BV 号换取 aid。失败返回 None。"""
    try:
        header, _ = _load_cookie_header(cookie_path)
        res = _bili_api(
            f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", header)
        if res.get("code") == 0:
            return int(res["data"]["aid"])
        logger.warning(f"获取 aid 失败: {res.get('code')} {res.get('message')}")
    except Exception as e:
        logger.warning(f"获取 aid 异常: {type(e).__name__}: {e}")
    return None


def post_video_comment(bvid: str, message: str,
                       cookie_path: str = "cookies.json",
                       pin: bool = True) -> Tuple[bool, str]:
    """在指定稿件下发表一条新评论（可选置顶）。

    Returns:
        (是否成功, rpid 字符串 或 错误信息)
    """
    try:
        header, jar = _load_cookie_header(cookie_path)
        csrf = jar.get("bili_jct", "")
        if not csrf:
            return False, "cookies.json 中缺少 bili_jct（CSRF），无法发评论"
        aid = get_aid_from_bvid(bvid, cookie_path)
        if not aid:
            return False, f"无法从 {bvid} 获取 aid"

        if len(message) > BILI_COMMENT_MAX_LEN:
            message = message[:BILI_COMMENT_MAX_LEN - 3] + "..."

        res = _bili_api("https://api.bilibili.com/x/v2/reply/add", header, {
            "type": "1", "oid": str(aid), "message": message, "csrf": csrf})
        if res.get("code") != 0:
            return False, f"发评论失败 code={res.get('code')} {res.get('message')}"

        rpid = (res.get("data") or {}).get("rpid")
        if pin and rpid:
            pres = _bili_api("https://api.bilibili.com/x/v2/reply/top", header, {
                "type": "1", "oid": str(aid), "action": "1",
                "rpid": str(rpid), "csrf": csrf})
            if pres.get("code") != 0:
                logger.warning(f"置顶失败 code={pres.get('code')} {pres.get('message')}")
                return True, f"{rpid}（置顶失败）"
        logger.info(f"B 站评论已发布{('并置顶' if pin else '')}: rpid={rpid}")
        return True, str(rpid)
    except Exception as e:
        return False, f"发评论异常: {type(e).__name__}: {e}"


def delete_video_comment(bvid: str, rpid: str,
                         cookie_path: str = "cookies.json") -> bool:
    """删除自己发的一条评论。"""
    try:
        header, jar = _load_cookie_header(cookie_path)
        csrf = jar.get("bili_jct", "")
        aid = get_aid_from_bvid(bvid, cookie_path)
        if not aid:
            return False
        res = _bili_api("https://api.bilibili.com/x/v2/reply/action", header, {
            "type": "1", "oid": str(aid), "rpid": str(rpid),
            "action": "0", "csrf": csrf})
        return res.get("code") == 0
    except Exception as e:
        logger.warning(f"删除评论异常: {type(e).__name__}: {e}")
        return False


def build_parts_index(parts: list, max_len: int = BILI_COMMENT_MAX_LEN) -> str:
    """把各分 P 的时间戳看点压缩成一条可置顶的索引评论。

    parts: [{'label': '【P1】', 'lines': ['01:30 xxx', ...]}, ...]
    """
    header = "📌 分P看点索引（点击时间戳可跳转到对应分P内的位置）"
    blocks = []
    for part in parts:
        label = part.get("label", "")
        lines = [l.strip() for l in part.get("lines", []) if l.strip()]
        if not lines:
            continue
        blocks.append(label + "\n" + "\n".join(lines))
    if not blocks:
        return header
    body = "\n\n".join(blocks)
    total = f"{header}\n\n{body}"
    if len(total) <= max_len:
        return total
    # 超长时逐块裁剪，保留尽可能多的分P
    kept = []
    used = len(header) + 2
    for block in blocks:
        add = len(block) + 2
        if used + add > max_len - 4:
            break
        kept.append(block)
        used += add
    if kept:
        omitted = len(blocks) - len(kept)
        suffix = f"\n\n...（其余 {omitted} 个分P看点见简介）" if omitted > 0 else ""
        return f"{header}\n\n" + "\n\n".join(kept) + suffix
    return header


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="B站评论接口诊断工具")
    parser.add_argument("bvid", help="稿件 BV 号")
    parser.add_argument("--cookie", default="cookies.json", help="cookies.json 路径")
    parser.add_argument("--probe-limit", action="store_true",
                        help="探测评论字数上限（发完自动删除）")
    parser.add_argument("--post", metavar="TEXT", help="发布一条评论")
    parser.add_argument("--pin", action="store_true", help="发布后置顶")
    parser.add_argument("--delete", metavar="RPID", help="删除指定 rpid 的评论")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.delete:
        print("删除结果:", delete_video_comment(args.bvid, args.delete, args.cookie))
    elif args.post:
        ok, info = post_video_comment(args.bvid, args.post, args.cookie, pin=args.pin)
        print(f"{'成功' if ok else '失败'}: {info}")
    elif args.probe_limit:
        import time as _t
        for n in (2000, 1500, 1001, 1000, 999):
            text = "字数上限探测" + "测" * max(0, n - 16) + f"[{n}]"
            ok, info = post_video_comment(args.bvid, text, args.cookie, pin=False)
            print(f"长度 {n:>5}: {'✅成功' if ok else '❌失败'}  {info[:80]}")
            if ok and info.isdigit():
                _t.sleep(2)
                print(f"          已删除: {delete_video_comment(args.bvid, info, args.cookie)}")
            _t.sleep(3)
    else:
        parser.print_help()

