from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

from tripletex.validators import validate_request


@dataclass
class ApiSummary:
    get: int = 0
    post: int = 0
    put: int = 0
    delete: int = 0
    client_4xx: int = 0
    client_5xx: int = 0
    calls: int = 0
    traces: list[dict[str, Any]] = field(default_factory=list)


class TripletexClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        session_token: str | None = None,
        timeout_seconds: float = 20,
    ) -> None:
        self.base_url = (base_url or os.getenv("TRIPLETEX_API_URL", "")).rstrip("/")
        self.session_token = session_token or os.getenv("TRIPLETEX_SESSION_TOKEN", "")
        self.timeout_seconds = timeout_seconds
        self.allowed_endpoints: set[str] | None = None
        self.summary = ApiSummary()

    def set_allowed_endpoints(self, endpoints: set[str]) -> None:
        self.allowed_endpoints = endpoints

    def _auth_header(self) -> dict[str, str]:
        token = base64.b64encode(f"0:{self.session_token}".encode("utf-8")).decode("ascii")
        return {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
        }

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", path, json_payload=payload)

    async def put(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._request("PUT", path, params=params, json_payload=payload)

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

        validated = validate_request(
            method=method,
            path=path,
            params=params,
            payload=json_payload,
            allowed_endpoints=self.allowed_endpoints,
        )
        clean_path = path if path.startswith("/") else f"/{path}"
        url = f"{self.base_url}{clean_path}"
        self.summary.calls += 1
        self.summary.traces.append(
            {
                "method": method,
                "path": clean_path,
                "params": validated.params,
                "has_payload": validated.payload is not None,
            }
        )
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.request(
                method=method,
                url=url,
                params=validated.params,
                json=validated.payload,
                headers=self._auth_header(),
                follow_redirects=False,
            )
        if method == "GET":
            self.summary.get += 1
        elif method == "POST":
            self.summary.post += 1
        elif method == "PUT":
            self.summary.put += 1
        elif method == "DELETE":
            self.summary.delete += 1

        if resp.status_code >= 500:
            self.summary.client_5xx += 1
        elif resp.status_code >= 400:
            self.summary.client_4xx += 1

        content_type = (resp.headers.get("content-type") or "").lower()
        body_text = resp.text if resp.content else ""
        body_text_l = body_text.lower()
        if "text/html" in content_type or "<html" in body_text_l or "<!doctype html" in body_text_l:
            self.summary.client_5xx += 1
            snippet = " ".join(body_text.split())[:220]
            raise RuntimeError(
                f"Tripletex {method} {clean_path} returned non-json response: "
                f"status={resp.status_code} content_type={content_type} snippet={snippet}"
            )

        if resp.status_code >= 400:
            body = body_text
            try:
                body = json.dumps(resp.json(), ensure_ascii=False)
            except Exception:
                pass
            raise RuntimeError(f"Tripletex {method} {clean_path} failed: {resp.status_code} body={body}")

        return resp.json() if resp.content else {}
