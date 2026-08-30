"""Docling-serve client.

Tries the stable v1 API first, falls back to the legacy v1alpha API.
"""
import logging
from typing import Any

import httpx

log = logging.getLogger("di.engine")


class EngineError(Exception):
    pass


CONTENT_TYPE_FORMATS = {
    "application/pdf": "pdf",
    "image/png": "image",
    "image/jpeg": "image",
    "image/tiff": "image",
    "image/bmp": "image",
    "image/webp": "image",
}


class DoclingClient:
    def __init__(self, base_url: str, timeout: float = 300.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def convert(self, filename: str, data: bytes, content_type: str) -> dict[str, Any]:
        fmt = CONTENT_TYPE_FORMATS.get(content_type, "pdf")
        form = {
            "to_formats": "json",
            "from_formats": fmt,
            "force_ocr": "false",
            "image_export_mode": "embedded",
            "table_mode": "fast",
            "ocr_engine": "easyocr",
            "ocr_lang": "en",
        }
        try:
            return await self._v1(filename, data, content_type, form)
        except EngineError as e:
            log.warning("v1 endpoint failed (%s), trying v1alpha", e)
            return await self._v1alpha(filename, data, content_type, form)

    async def _post_file(self, path: str, filename: str, data: bytes, content_type: str, form: dict):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(
                f"{self.base_url}{path}",
                files={"files": (filename, data, content_type)},
                data=form,
            )
        if r.status_code == 404:
            raise EngineError("endpoint not found (404)")
        if r.status_code >= 400:
            raise EngineError(f"engine HTTP {r.status_code}: {r.text[:300]}")
        return r

    async def _v1(self, filename: str, data: bytes, content_type: str, form: dict) -> dict[str, Any]:
        r = await self._post_file("/v1/convert/file", filename, data, content_type, form)
        payload = r.json()
        doc = payload.get("document") or {}
        if doc.get("json_content"):
            return doc["json_content"]
        # some versions return the document JSON directly
        if any(k in payload for k in ("pages", "texts", "tables")):
            return payload
        raise EngineError(f"engine returned no json_content: {str(payload)[:200]}")

    async def _v1alpha(self, filename: str, data: bytes, content_type: str, form: dict) -> dict[str, Any]:
        r = await self._post_file("/v1alpha/convert/file", filename, data, content_type, form)
        payload = r.json()
        doc = payload.get("document") or {}
        source_key = doc.get("source_key")
        if source_key:
            return await self._poll_v1alpha(source_key)
        if doc.get("json_content"):
            return doc["json_content"]
        raise EngineError("v1alpha returned no result")

    async def _poll_v1alpha(self, source_key: str) -> dict[str, Any]:
        import asyncio

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for _ in range(240):  # up to ~20 min
                r = await client.get(f"{self.base_url}/v1alpha/convert/source/{source_key}")
                if r.status_code >= 400:
                    raise EngineError(f"poll HTTP {r.status_code}")
                payload = r.json()
                status = (payload.get("document") or {}).get("status")
                if status in ("success", "partial_success") or "pages" in payload:
                    doc = payload.get("document") or {}
                    return doc.get("json_content") or payload
                if status == "failure":
                    raise EngineError("engine conversion failed")
                await asyncio.sleep(5)
        raise EngineError("engine conversion timed out")
