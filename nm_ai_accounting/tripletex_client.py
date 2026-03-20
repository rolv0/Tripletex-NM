from __future__ import annotations

import base64
import json
import os
from typing import Any

import httpx


class TripletexClient:
    def __init__(self, base_url: str | None = None, session_token: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("TRIPLETEX_API_URL", "")).rstrip("/")
        self.session_token = session_token or os.getenv("TRIPLETEX_SESSION_TOKEN", "")

    def _auth_header(self) -> dict[str, str]:
        if not self.session_token:
            return {}
        # Tripletex Basic auth is base64("0:<session_token>").
        token = base64.b64encode(f"0:{self.session_token}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", path, json_payload=payload)

    async def put(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", path, json_payload=payload)

    async def delete(self, path: str) -> dict[str, Any]:
        return await self._request("DELETE", path)

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.base_url:
            raise ValueError("Missing Tripletex base_url")
        if not self.session_token:
            raise ValueError("Missing Tripletex session_token")

        clean_path = path if path.startswith("/") else f"/{path}"
        url = f"{self.base_url}{clean_path}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(
                method=method,
                url=url,
                params=params,
                json=json_payload,
                headers=self._auth_header(),
            )
            if resp.status_code >= 400:
                body = resp.text
                try:
                    parsed = resp.json()
                    body = json_dumps_safe(parsed)
                except Exception:
                    pass
                raise RuntimeError(f"Tripletex {method} {clean_path} failed: {resp.status_code} body={body}")
            return resp.json() if resp.content else {}


def json_dumps_safe(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False)
    except Exception:
        return str(payload)
