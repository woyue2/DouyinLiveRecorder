# -*- encoding: utf-8 -*-
"""
Web 閱讀館門戶與索引生成模組
功能：
1. 掃描 web_posts/ 目錄下的所有文章 HTML，提取作者、發布日期、標題、看點摘要
2. 生成/更新 web_posts/posts_index.json，供門戶導航前端載入
3. 生成自包含、高顏值的響應式單頁閱讀館 index.html
"""

import os
import re
import json
import time
import glob
import logging
from typing import List, Dict, Any

logger = logging.getLogger("WebPortal")

PORTAL_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>AI 知識庫 · 精編閱讀館</title>
  <style>
    :root {
      --bg-page: #F3F4F6;
      --bg-sidebar: #FFFFFF;
      --bg-card: #FFFFFF;
      --text-main: #111827;
      --text-muted: #6B7280;
      --primary: #2563EB;
      --primary-light: #EFF6FF;
      --primary-border: #BFDBFE;
      --border: #E5E7EB;
      --sidebar-width: 340px;
      --header-height: 60px;
      --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
      --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg-page: #0F172A;
        --bg-sidebar: #1E293B;
        --bg-card: #1E293B;
        --text-main: #F8FAFC;
        --text-muted: #94A3B8;
        --primary: #3B82F6;
        --primary-light: #1E3A8A;
        --primary-border: #1D4ED8;
        --border: #334155;
        --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.3);
        --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.4);
      }
    }
    body.dark {
      --bg-page: #0F172A;
      --bg-sidebar: #1E293B;
      --bg-card: #1E293B;
      --text-main: #F8FAFC;
      --text-muted: #94A3B8;
      --primary: #3B82F6;
      --primary-light: #1E3A8A;
      --primary-border: #1D4ED8;
      --border: #334155;
    }
    body.light {
      --bg-page: #F3F4F6;
      --bg-sidebar: #FFFFFF;
      --bg-card: #FFFFFF;
      --text-main: #111827;
      --text-muted: #6B7280;
      --primary: #2563EB;
      --primary-light: #EFF6FF;
      --primary-border: #BFDBFE;
      --border: #E5E7EB;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      background: var(--bg-page);
      color: var(--text-main);
      height: 100vh;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }

    /* 頂部全局工具列 */
    header.portal-header {
      height: var(--header-height);
      background: var(--bg-sidebar);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 16px;
      z-index: 30;
      flex-shrink: 0;
    }
    .header-left { display: flex; align-items: center; gap: 12px; }
    .logo-badge {
      font-size: 17px;
      font-weight: 800;
      color: var(--primary);
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .header-controls { display: flex; align-items: center; gap: 8px; }
    .btn-icon {
      background: var(--bg-page);
      border: 1px solid var(--border);
      color: var(--text-main);
      width: 36px;
      height: 36px;
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      font-size: 15px;
      transition: all 0.2s;
    }
    .btn-icon:hover { background: var(--primary-light); color: var(--primary); }

    /* 漢堡按鈕 (手機端顯示) */
    .menu-toggle { display: none; }
    @media (max-width: 768px) {
      .menu-toggle { display: flex; }
    }

    /* 主佈局容器 */
    .portal-main {
      flex: 1;
      display: flex;
      height: calc(100vh - var(--header-height));
      position: relative;
      overflow: hidden;
    }

    /* 側邊欄 */
    aside.portal-sidebar {
      width: var(--sidebar-width);
      background: var(--bg-sidebar);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
      height: 100%;
      z-index: 25;
      transition: transform 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    }
    @media (max-width: 768px) {
      aside.portal-sidebar {
        position: absolute;
        top: 0;
        left: 0;
        bottom: 0;
        width: 85%;
        max-width: 340px;
        transform: translateX(-100%);
        box-shadow: 4px 0 16px rgba(0, 0, 0, 0.2);
      }
      aside.portal-sidebar.open {
        transform: translateX(0);
      }
    }

    /* 遮罩層 (手機端) */
    .sidebar-overlay {
      display: none;
      position: absolute;
      inset: 0;
      background: rgba(0, 0, 0, 0.45);
      z-index: 20;
      backdrop-filter: blur(2px);
    }
    .sidebar-overlay.active { display: block; }

    /* 搜尋框 */
    .search-box {
      padding: 12px 14px;
      border-bottom: 1px solid var(--border);
    }
    .search-input {
      width: 100%;
      padding: 8px 12px;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--bg-page);
      color: var(--text-main);
      font-size: 13.5px;
      outline: none;
    }
    .search-input:focus { border-color: var(--primary); }

    /* 側欄一級：主播分類膠囊列表 */
    .section-title {
      font-size: 11.5px;
      font-weight: 700;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      padding: 12px 16px 6px;
    }
    .author-list {
      display: flex;
      flex-direction: column;
      gap: 3px;
      padding: 4px 10px 10px;
      border-bottom: 1px solid var(--border);
    }
    .author-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 8px 12px;
      border-radius: 8px;
      cursor: pointer;
      font-size: 14px;
      font-weight: 600;
      color: var(--text-main);
      transition: all 0.15s;
    }
    .author-item:hover { background: var(--bg-page); }
    .author-item.active {
      background: var(--primary-light);
      color: var(--primary);
    }
    .count-badge {
      font-size: 11.5px;
      padding: 2px 7px;
      border-radius: 999px;
      background: var(--bg-page);
      color: var(--text-muted);
    }
    .author-item.active .count-badge {
      background: var(--primary);
      color: #FFFFFF;
    }

    /* 側欄二級：時間軸文章列表 */
    .timeline-container {
      flex: 1;
      overflow-y: auto;
      padding: 10px 12px 24px;
    }
    .month-group {
      margin-bottom: 16px;
    }
    .month-header {
      font-size: 12px;
      font-weight: 700;
      color: var(--text-muted);
      display: flex;
      align-items: center;
      gap: 6px;
      margin-bottom: 8px;
      padding-left: 4px;
    }
    .article-item {
      padding: 10px 12px;
      border-radius: 8px;
      cursor: pointer;
      margin-bottom: 6px;
      border: 1px solid transparent;
      background: var(--bg-card);
      box-shadow: var(--shadow-sm);
      transition: all 0.15s;
    }
    .article-item:hover {
      border-color: var(--primary-border);
      transform: translateY(-1px);
    }
    .article-item.active {
      border-color: var(--primary);
      background: var(--primary-light);
    }
    .article-item-title {
      font-size: 13.5px;
      font-weight: 600;
      color: var(--text-main);
      line-height: 1.45;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      margin-bottom: 4px;
    }
    .article-item-meta {
      font-size: 11.5px;
      color: var(--text-muted);
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .author-tag {
      color: var(--primary);
      font-weight: 600;
    }

    /* 右側主閱讀視圖 */
    main.portal-content {
      flex: 1;
      height: 100%;
      background: var(--bg-page);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      position: relative;
    }
    .iframe-container {
      flex: 1;
      width: 100%;
      height: 100%;
      border: none;
      background: transparent;
    }

    /* 空狀態展示 */
    .empty-state {
      position: absolute;
      inset: 0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      color: var(--text-muted);
      padding: 24px;
      text-align: center;
    }
    .empty-icon { font-size: 48px; margin-bottom: 16px; opacity: 0.6; }
  </style>
</head>
<body>
  <header class="portal-header">
    <div class="header-left">
      <button class="btn-icon menu-toggle" id="menuToggle" title="展開選單">☰</button>
      <div class="logo-badge">
        <span>🎙️</span>
        <span>AI 精編閱讀館</span>
      </div>
    </div>
    <div class="header-controls">
      <button class="btn-icon" id="themeToggle" title="切換深色/淺色模式">🌓</button>
      <button class="btn-icon" id="copyShareBtn" title="複製分享連結">🔗</button>
      <button class="btn-icon" id="openExternalBtn" title="在新分頁開啟全螢幕">↗️</button>
    </div>
  </header>

  <div class="portal-main">
    <div class="sidebar-overlay" id="sidebarOverlay"></div>

    <aside class="portal-sidebar" id="portalSidebar">
      <div class="search-box">
        <input type="text" class="search-input" id="searchInput" placeholder="🔍 搜尋標題、作者或看點...">
      </div>

      <div class="section-title">👤 主播分類 (Arrange by Author)</div>
      <div class="author-list" id="authorList">
        <!-- 動態渲染主播列表 -->
      </div>

      <div class="section-title" id="timelineTitle">📅 時間篇章 (Arrange by Time)</div>
      <div class="timeline-container" id="timelineContainer">
        <!-- 動態渲染時間篇章 -->
      </div>
    </aside>

    <main class="portal-content">
      <div class="empty-state" id="emptyState">
        <div class="empty-icon">📖</div>
        <h3>請在左側側邊欄選擇主播與時間篇章</h3>
        <p style="font-size: 13px; margin-top: 6px;">點選即可即時切換高顏值精編逐字稿</p>
      </div>
      <iframe class="iframe-container" id="articleFrame" style="display: none;" src="about:blank"></iframe>
    </main>
  </div>

  <script>
    // 預設或載入文章數據
    var ALL_POSTS = {posts_json};
    var currentAuthor = "ALL";
    var currentFile = "";

    var authorListEl = document.getElementById("authorList");
    var timelineContainerEl = document.getElementById("timelineContainer");
    var articleFrameEl = document.getElementById("articleFrame");
    var emptyStateEl = document.getElementById("emptyState");
    var searchInputEl = document.getElementById("searchInput");
    var portalSidebarEl = document.getElementById("portalSidebar");
    var sidebarOverlayEl = document.getElementById("sidebarOverlay");
    var menuToggleEl = document.getElementById("menuToggle");
    var themeToggleEl = document.getElementById("themeToggle");
    var copyShareBtnEl = document.getElementById("copyShareBtn");
    var openExternalBtnEl = document.getElementById("openExternalBtn");

    // 1. 初始化作者列表
    function renderAuthorList() {
      var authorCounts = {};
      ALL_POSTS.forEach(function(p) {
        var a = p.author || "未分類";
        authorCounts[a] = (authorCounts[a] || 0) + 1;
      });

      var html = '';
      html += '<div class="author-item ' + (currentAuthor === 'ALL' ? 'active' : '') + '" onclick="selectAuthor(\\'ALL\\')">';
      html += '  <span>👥 全部主播</span>';
      html += '  <span class="count-badge">' + ALL_POSTS.length + '</span>';
      html += '</div>';

      Object.keys(authorCounts).sort().forEach(function(author) {
        var isActive = (currentAuthor === author);
        html += '<div class="author-item ' + (isActive ? 'active' : '') + '" onclick="selectAuthor(\\'' + author + '\\')">';
        html += '  <span>🎙️ ' + author + '</span>';
        html += '  <span class="count-badge">' + authorCounts[author] + '</span>';
        html += '</div>';
      });

      authorListEl.innerHTML = html;
    }

    // 2. 切換作者
    function selectAuthor(author) {
      currentAuthor = author;
      renderAuthorList();
      renderTimeline();
    }

    // 3. 渲染二級時間軸
    function renderTimeline() {
      var keyword = (searchInputEl.value || '').trim().toLowerCase();
      var filtered = ALL_POSTS.filter(function(p) {
        if (currentAuthor !== 'ALL' && p.author !== currentAuthor) return false;
        if (keyword) {
          var matchTitle = (p.title || '').toLowerCase().indexOf(keyword) !== -1;
          var matchAuthor = (p.author || '').toLowerCase().indexOf(keyword) !== -1;
          var matchSummary = (p.summary || '').toLowerCase().indexOf(keyword) !== -1;
          return matchTitle || matchAuthor || matchSummary;
        }
        return true;
      });

      // 依發布日期由新到舊排序
      filtered.sort(function(a, b) {
        return (b.publish_time || '').localeCompare(a.publish_time || '');
      });

      // 按月份分組
      var groups = {};
      filtered.forEach(function(p) {
        var month = (p.publish_time || '').slice(0, 7) || '其他日期';
        if (!groups[month]) groups[month] = [];
        groups[month].push(p);
      });

      if (filtered.length === 0) {
        timelineContainerEl.innerHTML = '<div style="padding: 24px; text-align: center; color: var(--text-muted); font-size: 13px;">無匹配文章</div>';
        return;
      }

      var html = '';
      Object.keys(groups).sort().reverse().forEach(function(month) {
        html += '<div class="month-group">';
        html += '  <div class="month-header">📅 ' + month + ' (' + groups[month].length + ' 篇)</div>';
        groups[month].forEach(function(p) {
          var isActive = (currentFile === p.file);
          var shortDate = (p.publish_time || '').slice(5, 16);
          html += '<div class="article-item ' + (isActive ? 'active' : '') + '" onclick="selectArticle(\\'' + p.file + '\\')">';
          html += '  <div class="article-item-title">' + (p.title || p.file) + '</div>';
          html += '  <div class="article-item-meta">';
          if (currentAuthor === 'ALL') {
            html += '    <span class="author-tag">🎙️ ' + p.author + '</span><span>•</span>';
          }
          html += '    <span>' + shortDate + '</span>';
          html += '  </div>';
          html += '</div>';
        });
        html += '</div>';
      });

      timelineContainerEl.innerHTML = html;
    }

    // 4. 選取文章
    function selectArticle(file) {
      if (!file) return;
      currentFile = file;
      renderTimeline();

      emptyStateEl.style.display = 'none';
      articleFrameEl.style.display = 'block';
      articleFrameEl.src = file;

      // 更新 URL hash
      window.location.hash = '#/' + encodeURIComponent(file);

      // 手機端關閉抽屜
      closeSidebar();
    }

    // 5. 搜尋監聽
    searchInputEl.addEventListener('input', function() {
      renderTimeline();
    });

    // 6. 手機側邊欄開關
    function openSidebar() {
      portalSidebarEl.classList.add('open');
      sidebarOverlayEl.classList.add('active');
    }
    function closeSidebar() {
      portalSidebarEl.classList.remove('open');
      sidebarOverlayEl.classList.remove('active');
    }
    menuToggleEl.addEventListener('click', openSidebar);
    sidebarOverlayEl.addEventListener('click', closeSidebar);

    // 7. 深淺色模式
    var savedTheme = localStorage.getItem('portal_theme') || 'auto';
    if (savedTheme === 'dark') document.body.classList.add('dark');
    else if (savedTheme === 'light') document.body.classList.add('light');

    themeToggleEl.addEventListener('click', function() {
      if (document.body.classList.contains('dark')) {
        document.body.classList.remove('dark');
        document.body.classList.add('light');
        localStorage.setItem('portal_theme', 'light');
      } else {
        document.body.classList.remove('light');
        document.body.classList.add('dark');
        localStorage.setItem('portal_theme', 'dark');
      }
    });

    // 8. 複製與全螢幕
    copyShareBtnEl.addEventListener('click', function() {
      var url = window.location.href;
      if (navigator.clipboard) {
        navigator.clipboard.writeText(url).then(function() { alert('閱讀館分享連結已複製！'); });
      } else {
        prompt('請複製連結:', url);
      }
    });
    openExternalBtnEl.addEventListener('click', function() {
      if (currentFile) window.open(currentFile, '_blank');
    });

    // 9. 載入並恢復 hash 定位
    function init() {
      renderAuthorList();
      renderTimeline();

      // 檢查 URL hash
      var hash = window.location.hash || '';
      if (hash.startsWith('#/')) {
        var targetFile = decodeURIComponent(hash.slice(2));
        var found = ALL_POSTS.find(function(p) { return p.file === targetFile; });
        if (found) {
          if (found.author) selectAuthor(found.author);
          selectArticle(found.file);
          return;
        }
      }

      // 預設選中第一篇
      if (ALL_POSTS.length > 0) {
        selectArticle(ALL_POSTS[0].file);
      }
    }

    init();
  </script>
</body>
</html>
"""


def scan_posts_metadata(web_posts_dir: str) -> List[Dict[str, Any]]:
    """掃描目錄下所有 .html 文章並解析其元數據"""
    if not os.path.exists(web_posts_dir):
        return []

    posts = []
    html_files = glob.glob(os.path.join(web_posts_dir, "*.html"))

    for hf in html_files:
        filename = os.path.basename(hf)
        if filename in ("index.html", "portal.html"):
            continue

        try:
            with open(hf, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(8000)  # 只讀前 8KB 即可提取 metadata 與開頭摘要

            # 1. 提取作者
            author_match = re.search(r'<span class="badge">🎙️\s*(.*?)</span>', content)
            author = author_match.group(1).strip() if author_match else "未分類"

            # 2. 提取時間
            time_match = re.search(r'<span>📅\s*([\d\-:\s]+)</span>', content)
            publish_time = time_match.group(1).strip() if time_match else ""
            if not publish_time:
                # 從檔名前 8 碼推斷時間 (如 20261006_...)
                m_date = re.match(r'^(\d{4})(\d{2})(\d{2})_', filename)
                if m_date:
                    publish_time = f"{m_date.group(1)}-{m_date.group(2)}-{m_date.group(3)}"
                else:
                    publish_time = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(hf)))

            # 3. 提取標題
            title_match = re.search(r'<h1 class="article-title">(.*?)</h1>', content)
            title = title_match.group(1).strip() if title_match else filename.replace(".html", "")

            # 4. 提取看點摘要片段
            summary_snippet = ""
            summary_match = re.search(r'<div class="summary-card">(.*?)</div>', content, re.DOTALL)
            if summary_match:
                # 剔除 HTML 標籤
                summary_snippet = re.sub(r'<[^>]+>', ' ', summary_match.group(1)).strip()[:200]

            posts.append({
                "file": filename,
                "title": title,
                "author": author,
                "publish_time": publish_time,
                "summary": summary_snippet
            })
        except Exception as e:
            logger.warning(f"解析 {filename} 失敗: {e}")

    # 由新到舊排序
    posts.sort(key=lambda x: x["publish_time"], reverse=True)
    return posts


def update_portal(web_posts_dir: str) -> bool:
    """掃描文章、生成 index.json 並刷新 index.html"""
    if not os.path.exists(web_posts_dir):
        os.makedirs(web_posts_dir, exist_ok=True)

    posts = scan_posts_metadata(web_posts_dir)
    logger.info(f"掃描到 {len(posts)} 篇精編 HTML 文章")

    # 1. 寫入 posts_index.json
    index_json_path = os.path.join(web_posts_dir, "posts_index.json")
    try:
        with open(index_json_path, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"寫入 posts_index.json 失敗: {e}")

    # 2. 注入 JSON 寫入 index.html 門戶
    portal_index_path = os.path.join(web_posts_dir, "index.html")
    try:
        posts_json_str = json.dumps(posts, ensure_ascii=False)
        portal_html = PORTAL_HTML_TEMPLATE.replace("{posts_json}", posts_json_str)
        with open(portal_index_path, "w", encoding="utf-8") as f:
            f.write(portal_html)
        logger.info(f"✅ 成功刷新精編閱讀館門戶: {portal_index_path}")
        return True
    except Exception as e:
        logger.error(f"寫入 index.html 失敗: {e}")
        return False


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="生成與更新 AI 精編閱讀館門戶導航")
    parser.add_argument("--dir", type=str, default="./web_posts", help="HTML 文章目錄")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    update_portal(args.dir)
