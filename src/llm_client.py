# -*- encoding: utf-8 -*-
"""最小化的 LLM 聊天客户端，供精彩片段筛选等旁路功能使用。

刻意獨立於 src/bili_uploader.py：那裡的 LLM 呼叫和「標題/簡介 JSON 清洗」纏在
一起，改成通用會動到正在跑的投稿主路徑。這裡只提供「給 system+user，回文字」，
以及從回覆裡撈 JSON 兩個小工具，兩邊互不影響。
"""
import json
import re
import time
import urllib.error
import urllib.request
from typing import Optional

try:
    # 主程式用 loguru；標準 logging 沒有掛 handler，訊息會直接消失
    from loguru import logger
except ImportError:  # 測試環境不一定裝了 loguru
    import logging
    logger = logging.getLogger("llm_client")


def chat(system_prompt: str, user_content: str, api_key: str,
         api_base: Optional[str] = None, model: str = "deepseek-chat",
         max_tokens: int = 2048, temperature: float = 0.3,
         timeout: int = 60, retries: int = 2) -> Optional[str]:
    """呼叫兼容 OpenAI / Anthropic 協議的聊天接口，返回純文字。

    任何失敗都返回 None，由呼叫方決定回退策略（例如退回全量上傳）。
    """
    if not api_key:
        return None

    base_url = (api_base or "https://api.deepseek.com").rstrip("/")
    is_anthropic = "/anthropic" in base_url or "api.anthropic.com" in base_url

    if is_anthropic:
        url = base_url + "/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_content}],
        }
    else:
        url = base_url + ("/v1/chat/completions" if not base_url.endswith("/v1")
                          else "/chat/completions")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        payload = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }

    last_error = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"),
                headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if is_anthropic:
                return data["content"][0]["text"]
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code}"
            # 401/403（Key 錯）、400（模型名錯）重試一百次也不會好；
            # 429/408 與 5xx 才是暫時性的，值得再試
            if 400 <= e.code < 500 and e.code not in (408, 429):
                logger.warning(f"LLM 呼叫失敗（HTTP {e.code}，不重試）: {e.reason}")
                break
            logger.warning(f"LLM 呼叫失敗（第 {attempt + 1} 次）: {e}")
        except Exception as e:  # 網路、超時、格式都可能失敗
            last_error = str(e)
            logger.warning(f"LLM 呼叫失敗（第 {attempt + 1} 次）: {e}")
        if attempt < retries:
            time.sleep(2)
    logger.warning(f"LLM 呼叫最終失敗: {last_error}")
    return None


def extract_json(text: str):
    """從可能帶 markdown 圍欄或前後雜訊的回覆裡撈出 JSON 物件。

    模型偶爾會在字串裡塞未轉義換行，這裡用 strict=False 容錯；解析失敗回 None。
    """
    if not text:
        return None
    cleaned = re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*```$', '', cleaned.strip())
    match = re.search(r'\{[\s\S]*\}', cleaned)
    candidate = match.group(0) if match else cleaned
    try:
        return json.loads(candidate, strict=False)
    except Exception:
        return None
