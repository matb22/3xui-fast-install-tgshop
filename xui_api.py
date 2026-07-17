import os
import aiohttp
import logging
import json

logger = logging.getLogger(__name__)

class XuiAPI:
    def __init__(self, url, username=None, password=None):
        self.base_url = url.rstrip('/')
        prefix = os.getenv("XUI_PREFIX", "").strip('/')
        
        if prefix:
            self.url = f"{self.base_url}/{prefix}"
        else:
            self.url = self.base_url

        self.token = os.getenv("XUI_TOKEN", "").strip()
        self.session = None

    async def get_session(self):
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(ssl=False)
            self.session = aiohttp.ClientSession(connector=connector)
        return self.session

    def get_headers(self) -> dict:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def add_client(self, inbound_id: int, client_uuid: str, email: str, expiry_time_ms: int, total_gb: int = 0) -> bool:
        """
        Добавление клиента в панель.
        total_gb: лимит трафика в Гигабайтах (0 - безлимит).
        """
        session = await self.get_session()
        add_url = f"{self.url}/panel/api/clients/add"

        tg_id_val = 0
        try:
            if email.startswith("tg_"):
                parts = email.split("_")
                if len(parts) > 1 and parts[1].isdigit():
                    tg_id_val = int(parts[1])
        except Exception:
            tg_id_val = 0

        # 0 переданный сюда означает отсутствие лимита (безлимит на платных тарифах)
        total_bytes = total_gb * 1024 * 1024 * 1024 if total_gb > 0 else 0

        payload = {
            "client": {
                "email": email,
                "enable": True,
                "id": client_uuid,
                "expiryTime": expiry_time_ms,
                "totalGB": total_bytes,   
                "limitIp": 1,             # Строго 1 устройство для всех!
                "flow": "",
                "tgId": tg_id_val,
                "subId": client_uuid
            },
            "inboundIds": [int(inbound_id)]
        }

        try:
            headers = self.get_headers()
            async with session.post(add_url, json=payload, headers=headers, timeout=10) as r:
                status = r.status
                text = await r.text()
                if status in [200, 201]:
                    data = json.loads(text)
                    return data.get("success", False)
                return False
        except Exception as e:
            logger.error(f"Failed to add client: {e}")
            return False

    async def get_client_info(self, email: str) -> dict:
        session = await self.get_session()
        url = f"{self.url}/panel/api/clients/get/{email}"
        try:
            headers = self.get_headers()
            async with session.get(url, headers=headers, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    if data.get("success") and data.get("obj"):
                        return data["obj"]
        except Exception as e:
            logger.error(f"Error getting client info for {email}: {e}")
        return {}

    async def is_client_online(self, email: str) -> bool:
        """Проверяет, подключен ли этот клиент прямо сейчас"""
        session = await self.get_session()
        url = f"{self.url}/panel/api/clients/onlines"
        try:
            headers = self.get_headers()
            async with session.post(url, headers=headers, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    if data.get("success") and isinstance(data.get("obj"), list):
                        return email in data["obj"]
        except Exception as e:
            logger.error(f"Error checking online status for {email}: {e}")
        return False

    async def generate_sub_link(self, password: str) -> str:
        sub_host = "fufelshmertsvpn2.duckdns.org"
        sub_port = 10882  
        return f"http://{sub_host}:{sub_port}/sub/{password}"

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
