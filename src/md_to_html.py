# -*- encoding: utf-8 -*-
"""
Markdown 轉手機端優質排版 HTML 渲染引擎
專門為直播轉寫與短視頻內容打造：
1. 提取 <!--SUMMARY_START--> ... <!--SUMMARY_END--> 渲染為漸變 Hero 卡片
2. 對話說話人（主播、嘉賓、提問者）智慧膠囊徽章高亮
3. 100% 內嵌現代自包含 CSS，零外部 CDN 依賴，國內手機弱網秒開
4. 自適應深色模式 (Dark Mode)、頂部滾動進度條、一鍵複製連結
"""

import os
import re
import html
import time
import urllib.parse
from typing import Tuple, Optional


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>{page_title}</title>
  <style>
    :root {{
      --bg: #F8F9FA;
      --card-bg: #FFFFFF;
      --text-main: #1F2937;
      --text-secondary: #4B5563;
      --text-muted: #9CA3AF;
      --primary: #2563EB;
      --primary-light: #EFF6FF;
      --accent: #D97706;
      --accent-light: #FEF3C7;
      --border: #E5E7EB;
      --shadow: 0 4px 16px rgba(0, 0, 0, 0.06);
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #111827;
        --card-bg: #1F2937;
        --text-main: #F3F4F6;
        --text-secondary: #D1D5DB;
        --text-muted: #9CA3AF;
        --primary: #60A5FA;
        --primary-light: #1E3A8A;
        --accent: #FBBF24;
        --accent-light: #78350F;
        --border: #374151;
        --shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
      }}
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg);
      color: var(--text-main);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      line-height: 1.85;
      font-size: 16px;
      padding: 16px 12px 48px;
      -webkit-font-smoothing: antialiased;
    }}
    .progress-bar {{
      position: fixed;
      top: 0;
      left: 0;
      height: 3px;
      background: linear-gradient(90deg, var(--primary), var(--accent));
      z-index: 999;
      width: 0%;
      transition: width 0.1s ease-out;
    }}
    .container {{
      max-width: 680px;
      margin: 0 auto;
      background: var(--card-bg);
      border-radius: 16px;
      padding: 24px 20px;
      box-shadow: var(--shadow);
    }}
    .meta-bar {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
      font-size: 13px;
      color: var(--text-muted);
      margin-bottom: 16px;
      border-bottom: 1px solid var(--border);
      padding-bottom: 14px;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      padding: 2px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
      background: var(--primary-light);
      color: var(--primary);
    }}
    h1.article-title {{
      font-size: 22px;
      font-weight: 800;
      line-height: 1.45;
      margin-bottom: 16px;
      color: var(--text-main);
      letter-spacing: -0.01em;
    }}
    .summary-card {{
      background: linear-gradient(135deg, var(--primary-light) 0%, var(--accent-light) 100%);
      border: 1px solid var(--border);
      border-left: 5px solid var(--primary);
      border-radius: 14px;
      padding: 18px;
      margin: 20px 0 26px;
      font-size: 15px;
      color: var(--text-secondary);
    }}
    .summary-card strong {{
      color: var(--text-main);
    }}
    .summary-card ul, .summary-card ol {{
      margin-left: 20px;
      margin-top: 6px;
      margin-bottom: 10px;
    }}
    .summary-card li {{
      margin-bottom: 6px;
    }}
    .article-body h2 {{
      font-size: 19px;
      font-weight: 700;
      margin: 32px 0 14px;
      padding-left: 10px;
      border-left: 4px solid var(--primary);
      color: var(--text-main);
      display: flex;
      align-items: center;
    }}
    .article-body h3 {{
      font-size: 17px;
      font-weight: 600;
      margin: 24px 0 10px;
      color: var(--text-main);
    }}
    .article-body p {{
      margin-bottom: 16px;
      color: var(--text-secondary);
      word-break: break-word;
    }}
    .speaker-host {{
      display: inline-block;
      font-weight: 700;
      color: #2563EB;
      background: rgba(37, 99, 235, 0.08);
      padding: 1px 8px;
      border-radius: 6px;
      margin-right: 6px;
    }}
    .speaker-guest {{
      display: inline-block;
      font-weight: 700;
      color: #059669;
      background: rgba(5, 150, 105, 0.08);
      padding: 1px 8px;
      border-radius: 6px;
      margin-right: 6px;
    }}
    .footer-actions {{
      margin-top: 40px;
      padding-top: 18px;
      border-top: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 13px;
      color: var(--text-muted);
    }}
    .btn {{
      background: var(--primary-light);
      color: var(--primary);
      border: 1px solid var(--primary);
      padding: 7px 16px;
      border-radius: 8px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 600;
      transition: all 0.2s;
    }}
    .btn:active {{
      transform: scale(0.96);
    }}
  </style>
</head>
<body>
  <div class="progress-bar" id="progressBar"></div>
  <div class="container">
    <div class="meta-bar">
      <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
        <span class="badge">🎙️ {author}</span>
        <span>•</span>
        <span>📅 {publish_time}</span>
        <span>•</span>
        <span>⏱️ 视频转写精修</span>
      </div>
      <a id="backPortalBtn" href="index.html" target="_top" style="text-decoration: none; font-size: 12px; font-weight: 600; color: var(--primary); background: var(--primary-light); padding: 4px 10px; border-radius: 6px; border: 1px solid var(--primary); margin-left: auto;">📚 全部主播與目錄</a>
    </div>
    <h1 class="article-title">{article_title}</h1>

    {summary_html_card}

    <div class="article-body">
      {article_body_html}
    </div>

    <div class="footer-actions">
      <span>生成于 {generate_time}</span>
      <button class="btn" onclick="copyShareLink()">🔗 复制链接</button>
    </div>
  </div>

  <script>
    window.onscroll = function() {{
      var winScroll = document.body.scrollTop || document.documentElement.scrollTop;
      var height = document.documentElement.scrollHeight - document.documentElement.clientHeight;
      var scrolled = (height > 0) ? (winScroll / height) * 100 : 0;
      document.getElementById("progressBar").style.width = scrolled + "%";
    }};

    // 如果是在門戶 iframe 內嵌展示，隱藏返回目錄按鈕
    if (window.self !== window.top) {{
      var backBtn = document.getElementById("backPortalBtn");
      if (backBtn) backBtn.style.display = "none";
    }}

    function copyShareLink() {{
      if (navigator.clipboard) {{
        navigator.clipboard.writeText(location.href).then(function() {{
          alert('文章链接已复制到剪贴板！');
        }});
      }} else {{
        var t = document.createElement("input");
        t.value = location.href;
        document.body.appendChild(t);
        t.select();
        document.execCommand("copy");
        document.body.removeChild(t);
        alert('文章链接已复制到剪贴板！');
      }}
    }}
  </script>
</body>
</html>
"""


def render_markdown_inline(text: str) -> str:
    """輕量 Markdown 內聯標籤渲染 (加粗、高亮、對話標籤)"""
    # 轉義 HTML 避免 XSS
    text = html.escape(text)

    # 角色對話標籤優化
    text = re.sub(
        r'&lt;b&gt;主播：&lt;/b&gt;|\*\*主播：\*\*|\*\*主播:\*\*',
        r'<span class="speaker-host">🎙️ 主播</span>',
        text
    )
    text = re.sub(
        r'&lt;b&gt;提问者：&lt;/b&gt;|\*\*提问者：\*\*|\*\*提问者:\*\*|\*\*观众：\*\*|\*\*观众:\*\*',
        r'<span class="speaker-guest">💬 提问者</span>',
        text
    )

    # **粗體** -> <strong>
    text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)
    # *斜體* -> <em>
    text = re.sub(r'\*(.*?)\*', r'<em>\1</em>', text)
    # `行內代碼` -> <code>
    text = re.sub(r'`(.*?)`', r'<code>\1</code>', text)

    return text


def parse_summary_block(summary_text: str) -> str:
    """將摘要內容解析為卡片 HTML"""
    if not summary_text.strip():
        return ""

    lines = summary_text.strip().splitlines()
    body_lines = []
    in_list = False

    for line in lines:
        clean = line.lstrip('> ').strip()
        if not clean:
            if in_list:
                body_lines.append("</ul>")
                in_list = False
            continue

        # 檢測數字清單或符號清單
        list_match = re.match(r'^(\d+\.|\-|\*)\s*(.*)', clean)
        if list_match:
            if not in_list:
                body_lines.append("<ul>")
                in_list = True
            body_lines.append(f"<li>{render_markdown_inline(list_match.group(2))}</li>")
        else:
            if in_list:
                body_lines.append("</ul>")
                in_list = False
            body_lines.append(f"<p>{render_markdown_inline(clean)}</p>")

    if in_list:
        body_lines.append("</ul>")

    content_html = "\n".join(body_lines)
    return f"""<div class="summary-card">
  {content_html}
</div>"""


def is_meaningful_title(candidate: str) -> bool:
    """判斷標題是否具備實際語意（排除空字串與純 hashtag 文案）"""
    if not candidate or not candidate.strip():
        return False
    without_tags = re.sub(r'#[^\s#]+', '', candidate).strip()
    return bool(without_tags)


def extract_article_title(md_path: str) -> str:
    """
    讀取 Markdown 中 LLM 生成的 H1 總標題。
    若該標題不存在或只是純 hashtag 文案，回傳空字串，交由呼叫方回退。
    """
    if not os.path.exists(md_path):
        return ""
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return ""

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith('## '):
            continue
        if stripped.startswith('# '):
            candidate = stripped[2:].strip()
            return candidate if is_meaningful_title(candidate) else ""
    return ""


def md_to_html(md_content: str, author: str = "", title: str = "", publish_time: str = "") -> str:
    """核心轉換邏輯：Markdown 字符串 -> 完整 HTML 頁面"""
    # 1. 提取 <!--SUMMARY_START--> 區塊
    summary_html = ""
    summary_match = re.search(r'<!--SUMMARY_START-->\s*(.*?)\s*<!--SUMMARY_END-->', md_content, re.DOTALL)
    if summary_match:
        summary_raw = summary_match.group(1).strip()
        summary_html = parse_summary_block(summary_raw)
        # 移除已提取的摘要部分
        md_content = md_content.replace(summary_match.group(0), "")

    # 2. 提取一級標題 # Title（優先採用 LLM 生成的標題，平台文案僅作為回退）
    md_h1_title = ""
    lines = md_content.strip().splitlines()
    body_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith('# '):
            if not md_h1_title:
                md_h1_title = stripped[2:].strip()
            continue  # 主標題已由模板的 h1.article-title 呈現
        elif stripped.startswith('## '):
            h2_text = render_markdown_inline(stripped[3:].strip())
            body_lines.append(f"<h2>{h2_text}</h2>")
        elif stripped.startswith('### '):
            h3_text = render_markdown_inline(stripped[4:].strip())
            body_lines.append(f"<h3>{h3_text}</h3>")
        elif stripped.startswith('- ') or stripped.startswith('* '):
            li_text = render_markdown_inline(stripped[2:].strip())
            body_lines.append(f"<ul><li>{li_text}</li></ul>")
        else:
            p_text = render_markdown_inline(stripped)
            body_lines.append(f"<p>{p_text}</p>")

    article_body_html = "\n".join(body_lines)
    now_str = time.strftime("%Y-%m-%d %H:%M")

    # 優先採用 LLM 生成的標題；僅在其缺失或為純 hashtag 文案時回退至平台文案
    article_title = md_h1_title if is_meaningful_title(md_h1_title) else title

    return HTML_TEMPLATE.format(
        page_title=f"{article_title or author or '精華轉寫'} - 在線閱讀",
        author=author or "抖音博主",
        publish_time=publish_time or "近期發布",
        article_title=article_title or "視頻內容精修逐字稿",
        summary_html_card=summary_html,
        article_body_html=article_body_html,
        generate_time=now_str
    )


def convert_md_file_to_html(
    md_path: str,
    output_dir: str,
    author: str = "",
    title: str = "",
    publish_time: str = ""
) -> Tuple[Optional[str], Optional[str]]:
    """
    讀取一個 Markdown 檔案並在 output_dir 下生成同名 .html
    返回 (html_file_path, html_filename)
    """
    if not os.path.exists(md_path):
        return None, None

    try:
        with open(md_path, "r", encoding="utf-8") as f:
            md_text = f.read()

        html_text = md_to_html(md_text, author=author, title=title, publish_time=publish_time)

        os.makedirs(output_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(md_path))[0]
        html_filename = f"{base_name}.html"
        html_path = os.path.join(output_dir, html_filename)

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_text)

        return html_path, html_filename
    except Exception as e:
        print(f"Markdown 轉 HTML 失敗: {e}")
        return None, None
