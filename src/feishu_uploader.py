# -*- encoding: utf-8 -*-
"""
飛書雲文檔 (Feishu Docx) 自動匯入與發布模組
功能：
1. 獲取並緩存飛書自建應用的 tenant_access_token (有效期 2 小時)
2. 調用官方 Drive API 將清洗提煉的 Markdown 自動匯入為飛書原生 Docx 雲文檔
3. 自動清理 <!--SUMMARY_START--> 等 HTML 標註，轉為飛書最佳閱讀體驗的引用塊
4. 返回飛書文檔直達 URL (https://my.feishu.cn/docx/...) 供釘釘/即時推送
"""

import os
import re
import time
import logging
from typing import Optional, Tuple, Dict, Any

import httpx

logger = logging.getLogger("FeishuUploader")

FEISHU_HOST = "https://open.feishu.cn"


class FeishuDocManager:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        folder_token: str = "",
        wiki_space_id: str = "",
        wiki_root_node: str = "",
        wiki_domain: str = "my.feishu.cn",
    ):
        self.app_id = app_id.strip()
        self.app_secret = app_secret.strip()
        self.folder_token = folder_token.strip()
        self.wiki_space_id = wiki_space_id.strip()
        self.wiki_root_node = wiki_root_node.strip()
        self.wiki_domain = (wiki_domain or "my.feishu.cn").strip()
        self._token: Optional[str] = None
        self._token_expire_at: float = 0
        # 父資料夾 token -> {子資料夾名稱: 子資料夾 token} 快取，避免同一次執行重複查詢
        self._subfolder_cache: Dict[str, Dict[str, str]] = {}
        # 主播名稱 -> 知識庫 node_token 快取
        self._wiki_author_cache: Dict[str, str] = {}
        self.client = httpx.Client(timeout=30.0)

    @property
    def wiki_enabled(self) -> bool:
        return bool(self.wiki_space_id and self.wiki_root_node)

    def _wiki_headers(self) -> Optional[Dict[str, str]]:
        token = self.get_tenant_access_token()
        if not token:
            return None
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def list_wiki_child_nodes(self, parent_node_token: str) -> Dict[str, str]:
        """列出知識庫中某節點下的子節點，回傳 {標題: node_token}"""
        headers = self._wiki_headers()
        if not headers or not self.wiki_space_id:
            return {}

        url = f"{FEISHU_HOST}/open-apis/wiki/v2/spaces/{self.wiki_space_id}/nodes"
        nodes: Dict[str, str] = {}
        page_token = ""
        try:
            while True:
                params = {"parent_node_token": parent_node_token, "page_size": 50}
                if page_token:
                    params["page_token"] = page_token
                data = self.client.get(url, headers=headers, params=params).json()
                if data.get("code") != 0:
                    logger.warning(f"列出知識庫節點失敗: {data.get('msg')}")
                    break
                payload = data.get("data", {}) or {}
                for item in payload.get("items") or []:
                    nodes[item.get("title", "")] = item.get("node_token", "")
                if payload.get("has_more"):
                    page_token = payload.get("page_token", "")
                else:
                    break
        except Exception as e:
            logger.warning(f"列出知識庫節點異常: {e}")
        return nodes

    def get_or_create_wiki_author_node(self, author: str) -> Optional[str]:
        """取得或建立知識庫中該主播的節點，回傳 node_token"""
        clean_author = (author or "").strip()
        if not self.wiki_enabled or not clean_author:
            return None

        if clean_author in self._wiki_author_cache:
            return self._wiki_author_cache[clean_author]

        existing = self.list_wiki_child_nodes(self.wiki_root_node)
        if clean_author in existing and existing[clean_author]:
            self._wiki_author_cache.update(existing)
            return self._wiki_author_cache[clean_author]

        headers = self._wiki_headers()
        if not headers:
            return None
        try:
            resp = self.client.post(
                f"{FEISHU_HOST}/open-apis/wiki/v2/spaces/{self.wiki_space_id}/nodes",
                headers=headers,
                json={
                    "obj_type": "docx",
                    "node_type": "origin",
                    "parent_node_token": self.wiki_root_node,
                    "title": clean_author,
                },
            )
            data = resp.json()
            if data.get("code") == 0:
                node_token = data.get("data", {}).get("node", {}).get("node_token")
                if node_token:
                    self._wiki_author_cache[clean_author] = node_token
                    logger.info(f"已在知識庫建立主播節點: {clean_author}")
                    return node_token
            logger.warning(f"建立知識庫主播節點失敗 ({clean_author}): {data.get('msg')}")
        except Exception as e:
            logger.warning(f"建立知識庫主播節點異常 ({clean_author}): {e}")
        return None

    def move_doc_to_wiki(self, parent_node_token: str, obj_token: str) -> Optional[str]:
        """把雲空間中的 Docx 移入知識庫指定節點下，回傳新的 wiki node_token"""
        headers = self._wiki_headers()
        if not headers or not obj_token:
            return None
        try:
            resp = self.client.post(
                f"{FEISHU_HOST}/open-apis/wiki/v2/spaces/{self.wiki_space_id}/nodes/move_docs_to_wiki",
                headers=headers,
                json={"parent_wiki_token": parent_node_token, "obj_type": "docx", "obj_token": obj_token},
            )
            data = resp.json()
            if data.get("code") != 0:
                logger.warning(f"移入知識庫失敗: {data.get('msg')}")
                return None

            # 反查節點，取得 wiki node_token 以組成可分享網址
            lookup = self.client.get(
                f"{FEISHU_HOST}/open-apis/wiki/v2/spaces/get_node",
                headers=headers,
                params={"token": obj_token, "obj_type": "docx"},
            ).json()
            node_token = (lookup.get("data", {}) or {}).get("node", {}).get("node_token")
            return node_token
        except Exception as e:
            logger.warning(f"移入知識庫異常: {e}")
            return None

    def create_article_docx(
        self,
        md_path: str,
        author: str = "",
        doc_title: str = "",
        folder_token: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        建立文章 Docx 並歸檔：
        1. 匯入 Markdown 為 Docx
        2. 若啟用知識庫，移入該主播的知識庫節點，回傳知識庫網址
        3. 否則保留在雲空間主播子資料夾
        """
        target_folder = folder_token
        if target_folder is None:
            target_folder = self.get_or_create_author_folder(author) if author else self.folder_token

        doc_url, doc_token = self.create_docx_from_markdown(
            md_path, doc_title=doc_title, folder_token=target_folder
        )
        if not doc_token:
            return None, None

        if self.wiki_enabled and author:
            author_node = self.get_or_create_wiki_author_node(author)
            if author_node:
                wiki_node = self.move_doc_to_wiki(author_node, doc_token)
                if wiki_node:
                    return f"https://{self.wiki_domain}/wiki/{wiki_node}", doc_token

        return doc_url, doc_token

    def _list_subfolders(self, parent_token: str) -> Dict[str, str]:
        """列出父資料夾下的所有子資料夾 {名稱: token}"""
        token = self.get_tenant_access_token()
        if not token:
            return {}

        headers = {"Authorization": f"Bearer {token}"}
        url = f"{FEISHU_HOST}/open-apis/drive/v1/files"
        params = {"folder_token": parent_token, "page_size": 200}
        folders: Dict[str, str] = {}

        try:
            resp = self.client.get(url, headers=headers, params=params)
            data = resp.json()
            if data.get("code") != 0:
                logger.warning(f"列出飛書子資料夾失敗: {data.get('msg')}")
                return {}
            for item in data.get("data", {}).get("files", []):
                if item.get("type") == "folder":
                    folders[item.get("name", "")] = item.get("token", "")
        except Exception as e:
            logger.warning(f"列出飛書子資料夾異常: {e}")

        return folders

    def _create_folder(self, name: str, parent_token: str) -> Optional[str]:
        """在父資料夾下建立子資料夾，返回新資料夾 token"""
        token = self.get_tenant_access_token()
        if not token:
            return None

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        url = f"{FEISHU_HOST}/open-apis/drive/v1/files/create_folder"
        try:
            resp = self.client.post(url, headers=headers, json={"name": name, "folder_token": parent_token})
            data = resp.json()
            if data.get("code") == 0:
                return data.get("data", {}).get("token")
            logger.warning(f"建立飛書子資料夾失敗 ({name}): {data.get('msg')}")
        except Exception as e:
            logger.warning(f"建立飛書子資料夾異常 ({name}): {e}")
        return None

    def get_or_create_author_folder(self, author: str, parent_folder_token: str = "") -> str:
        """
        取得或建立以主播命名的子資料夾，讓每位主播的文章歸類在自己的資料夾下。
        失敗時回退至父資料夾，確保不影響文檔創建。
        """
        parent = parent_folder_token or self.folder_token
        clean_author = (author or "").strip()
        if not parent or not clean_author:
            return parent

        if parent not in self._subfolder_cache:
            self._subfolder_cache[parent] = self._list_subfolders(parent)

        folders = self._subfolder_cache[parent]
        if clean_author in folders and folders[clean_author]:
            return folders[clean_author]

        new_token = self._create_folder(clean_author, parent)
        if new_token:
            folders[clean_author] = new_token
            logger.info(f"已建立主播子資料夾: {clean_author}")
            return new_token

        return parent

    def is_configured(self) -> bool:
        """檢查是否已配置 App ID 與 App Secret"""
        return bool(self.app_id and self.app_secret)

    def get_tenant_access_token(self, force_refresh: bool = False) -> Optional[str]:
        """取得並緩存 tenant_access_token"""
        now = time.time()
        if not force_refresh and self._token and now < self._token_expire_at:
            return self._token

        if not self.is_configured():
            logger.warning("未配置飛書 app_id 或 app_secret，跳過飛書上傳")
            return None

        url = f"{FEISHU_HOST}/open-apis/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self.app_id,
            "app_secret": self.app_secret
        }

        try:
            resp = self.client.post(url, json=payload)
            data = resp.json()
            if data.get("code") == 0:
                self._token = data.get("tenant_access_token")
                # 緩存約 110 分鐘 (官方過期時間為 120 分鐘)
                self._token_expire_at = now + 6600
                return self._token
            else:
                logger.error(f"獲取飛書 tenant_access_token 失敗: {data.get('msg')} (code: {data.get('code')})")
                return None
        except Exception as e:
            logger.error(f"請求飛書 Token 異常: {e}")
            return None

    def _prepare_cleaned_markdown(self, md_path: str, temp_path: str) -> bool:
        """預處理 Markdown：將 <!--SUMMARY_START--> 轉為乾淨的引用塊，移除多餘 HTML 註釋"""
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 移除 summary 註釋標記，保留內部內容
            cleaned = re.sub(r'<!--SUMMARY_START-->\s*', '', content)
            cleaned = re.sub(r'\s*<!--SUMMARY_END-->', '', cleaned)

            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(cleaned)
            return True
        except Exception as e:
            logger.warning(f"處理臨時 Markdown 失敗: {e}")
            return False

    def append_text_block(self, document_id: str, text: str) -> bool:
        """在既有 Docx 文檔末尾追加一段純文字。

        用途：直播時間軸在收播時先建好，投稿成功後把 BV 號與分 P 對照補進來。
        document_id 是 Docx 的 obj_token（create_article_docx 回傳的第二個值），
        不是 wiki 節點 token。
        """
        token = self.get_tenant_access_token()
        if not token:
            logger.error("追加文檔內容失敗: 無法取得 tenant_access_token")
            return False
        url = (f"{FEISHU_HOST}/open-apis/docx/v1/documents/{document_id}"
               f"/blocks/{document_id}/children?document_revision_id=-1")
        payload = {
            "index": -1,
            "children": [{
                "block_type": 2,  # text
                "text": {"elements": [{"text_run": {"content": text}}]},
            }],
        }
        try:
            resp = httpx.post(url, headers={"Authorization": f"Bearer {token}"},
                              json=payload, timeout=15.0)
            data = resp.json()
        except Exception as e:
            logger.error(f"追加文檔內容異常: {e}")
            return False
        if data.get("code") == 0:
            return True
        logger.error(f"追加文檔內容失敗: {data.get('code')} {data.get('msg')}")
        return False

    def create_docx_from_markdown(
        self,
        md_path: str,
        doc_title: str = "",
        folder_token: Optional[str] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        將 Markdown 文件匯入為飛書原生 Docx 雲文檔
        返回: (doc_url, doc_token)
        """
        if not os.path.exists(md_path):
            logger.error(f"Markdown 檔案不存在: {md_path}")
            return None, None

        token = self.get_tenant_access_token()
        if not token:
            return None, None

        target_folder = folder_token if folder_token is not None else self.folder_token
        headers = {"Authorization": f"Bearer {token}"}

        # 預處理臨時 md 文件
        temp_md = md_path + ".feishu.tmp"
        if not self._prepare_cleaned_markdown(md_path, temp_md):
            temp_md = md_path

        file_name = f"{doc_title}.md" if doc_title else os.path.basename(md_path)
        file_size = os.path.getsize(temp_md)

        file_token = None
        try:
            # 1. 上傳 Markdown 檔案到雲空間
            logger.info(f"正在將 Markdown 上傳至飛書雲空間: {file_name}")
            upload_url = f"{FEISHU_HOST}/open-apis/drive/v1/files/upload_all"
            data = {
                "file_name": file_name,
                "parent_type": "explorer",
                "parent_node": target_folder,  # 留空則存入個人空間根目錄
                "size": str(file_size)
            }

            with open(temp_md, "rb") as f:
                files = {"file": (file_name, f, "text/markdown")}
                resp_up = self.client.post(upload_url, headers=headers, data=data, files=files)

            up_json = resp_up.json()
            if up_json.get("code") != 0:
                logger.error(f"上傳 Markdown 到飛書失敗: {up_json.get('msg')} (code: {up_json.get('code')})")
                return None, None

            file_token = up_json["data"]["file_token"]

            # 2. 創建 Docx 導入任務
            logger.info(f"正在發起飛書 Docx 導入轉換任務 (file_token: {file_token})...")
            task_url = f"{FEISHU_HOST}/open-apis/drive/v1/import_tasks"
            task_payload = {
                "file_extension": "md",
                "file_token": file_token,
                "type": "docx",
                "point": {
                    "mount_type": 1,
                    "mount_key": target_folder
                }
            }
            resp_task = self.client.post(task_url, headers=headers, json=task_payload)
            task_json = resp_task.json()
            if task_json.get("code") != 0:
                logger.error(f"創建飛書導入任務失敗: {task_json.get('msg')} (code: {task_json.get('code')})")
                return None, None

            ticket = task_json.get("data", {}).get("ticket")
            if not ticket:
                logger.error("飛書未返回導入任務 ticket")
                return None, None

            # 3. 輪詢導入任務狀態 (最長等待 25 秒)
            poll_url = f"{FEISHU_HOST}/open-apis/drive/v1/import_tasks/{ticket}"
            start_poll = time.time()
            while time.time() - start_poll < 25.0:
                time.sleep(1.2)
                poll_resp = self.client.get(poll_url, headers=headers)
                poll_data = poll_resp.json().get("data", {}).get("result", {})
                job_status = poll_data.get("job_status")

                if job_status == 0:  # 0 表示成功
                    doc_url = poll_data.get("url")
                    doc_token = poll_data.get("token")
                    logger.info(f"🎉 飛書 Docx 雲文檔創建成功！URL: {doc_url}")
                    # 清理雲空間中殘留的中間 .md 來源檔，僅保留最終 Docx
                    try:
                        self.client.delete(
                            f"{FEISHU_HOST}/open-apis/drive/v1/files/{file_token}?type=file",
                            headers=headers
                        )
                    except Exception:
                        pass
                    return doc_url, doc_token
                elif job_status in (1, 2):  # 隊列中或轉換中
                    continue
                else:
                    logger.error(f"飛書 Docx 轉換失敗: {poll_data}")
                    return None, None

            logger.error("飛書導入任務輪詢超時")
            return None, None

        except Exception as e:
            logger.error(f"創建飛書雲文檔異常: {e}")
            return None, None
        finally:
            if os.path.exists(temp_md) and temp_md != md_path:
                try:
                    os.remove(temp_md)
                except OSError:
                    pass
