# -*- coding: utf-8 -*-
"""檔名清洗工具。

獨立成一個只依賴 re 的小模組，避免被 src/utils.py 的重型依賴（execjs 等）
牽連；抖音監控、小紅書監控與任何會寫檔名的地方都應共用這一份規則。
"""

import re


def sanitize_filename_part(text: str, max_len: int = 25) -> str:
    """清洗檔名片段：過濾 Emoji、高位 Unicode 符號與檔案系統非法字元。

    百度網盤 PCS API（bypy）在檔名含 Emoji（例如 🔥、💡 等 4 位元組字符）
    或部分特殊標點時會拒絕建立並回報 Error 31062 (file name is invalid)，
    因此所有會落到 downloads/ 的檔名都必須經過這裡，避免各模組各有一套
    清洗規則而逐漸分歧。
    """
    if not text:
        return ""
    clean = re.sub(r'[\U00010000-\U0010ffff]|[☀-➿]|[︀-️]', '', text)
    clean = re.sub(r'[\s#\\/*?:"<>|`~$\^&%!\'\"]', '_', clean[:max_len]).strip('_')
    return clean
