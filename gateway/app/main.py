"""Azure Document Intelligence drop-in API gateway."""
import asyncio
import logging
import time
from typing import Optional

import httpx
from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse

from . import config, engine as engine_mod, jobs as jobs_mod
from .mapper import (DocumentFeatures, build_analyze_result, map_invoice, map_po)

log = logging.getLogger("di.main")

app = FastAPI(title="DocIntel-Local", docs_url=None, redoc_url=None)
store = jobs_mod.JobStore()
engine = engine_mod.DoclingClient(config.DOCLING_URL, config.ENGINE_TIMEOUT)

SUPPORTED_MODELS = {"prebuilt-invoice", "prebuilt-po", "prebuilt-layout", "prebuilt-read"}
FILE_CONTENT_TYPES = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg", "image/png": "png",
    "image/tiff": "tif", "image/bmp": "bmp",
}


def az_error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _check_auth(key_header: Optional[str]) -> Optional[JSONResponse]:
    if not config.API_KEY:
        return None
    if key_header != config.API_KEY:
        return az_error(
            "401", "Access denied due to invalid subscription key or wrong API endpoint.", 401)
    return None


def _check_model(model_id: str) -> Optional[JSONResponse]:
    if model_id not in SUPPORTED_MODELS:
        return az_error("NotFound", f"The requested model '{model_id}' was not found.", 404)
    return None


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/formrecognizer/documentModels/{model_id}:analyze")
async def analyze(
    model_id: str,
    request: Request,
    api_version: str = Query(default="2023-07-31", alias="api-version"),
    subscription_key: Optional[str] = Header(default=None, alias="Ocp-Apim-Subscription-Key"),
):
    if api_version not in config.SUPPORTED_API_VERSIONS:
        return az_error("InvalidApiVersion", f"Unsupported API version '{api_version}'.", 400)
    if err := _check_auth(subscription_key):
        return err
    if err := _check_model(model_id):
        return err

    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    filename, data = None, None

    if content_type == "application/json":
        try:
            body = await request.json()
        except Exception:
            return az_error("InvalidRequest", "Request body is not valid JSON.", 400)
        url = body.get("urlSource") if isinstance(body, dict) else None
        if not url or not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return az_error("InvalidRequest", "urlSource must be an http(s) URL.", 400)
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                r = await client.get(url)
            if r.status_code >= 400:
                return az_error("InvalidRequest", f"urlSource fetch failed: HTTP {r.status_code}.", 400)
            data = r.content
            filename = url.rsplit("/", 1)[-1].split("?")[0] or "document"
            content_type = (r.headers.get("content-type") or "application/pdf").split(";")[0]
        except httpx.HTTPError as e:
            return az_error("InvalidRequest", f"urlSource fetch failed: {e}", 400)
    elif content_type in FILE_CONTENT_TYPES:
        try:
            body = await request.body()
        except Exception:
            return az_error("InvalidRequest", "Could not read request body.", 400)
        max_bytes = config.MAX_UPLOAD_MB * 1024 * 1024
        if len(body) > max_bytes:
            return az_error("InvalidRequest",
                            f"Request body exceeds {config.MAX_UPLOAD_MB} MB limit.", 413)
        data, filename = body, f"document.{FILE_CONTENT_TYPES[content_type]}"
    else:
        return az_error("InvalidRequest",
                        f"Unsupported Content-Type '{content_type}'. "
                        "Use application/pdf, image/*, or application/json with urlSource.", 400)

    job = store.new(model_id, api_version)
    asyncio.create_task(_process(job, model_id, filename, data, content_type))
    location = (f"{request.base_url}formrecognizer/documentModels/{model_id}"
                f"/analyzeResults/{job.result_id}?api-version={api_version}")
    return JSONResponse(status_code=202, headers={"Operation-Location": location}, content={})


@app.get("/formrecognizer/documentModels/{model_id}/analyzeResults/{result_id}")
async def get_result(model_id: str, result_id: str,
                     api_version: str = Query(default="2023-07-31", alias="api-version"),
                     subscription_key: Optional[str] = Header(default=None, alias="Ocp-Apim-Subscription-Key")):
    if api_version not in config.SUPPORTED_API_VERSIONS:
        return az_error("InvalidApiVersion", f"Unsupported API version '{api_version}'.", 400)
    if err := _check_auth(subscription_key):
        return err
    if err := _check_model(model_id):
        return err
    job = store.get(result_id)
    if job is None or job.model_id != model_id:
        return az_error("NotFound", f"The requested result '{result_id}' was not found.", 404)
    return JSONResponse(status_code=200, content=job.public())


async def _process(job: jobs_mod.Job, model_id: str, filename: str,
                   data: bytes, content_type: str) -> None:
    started = time.time()
    try:
        doc = await engine.convert(filename, data, content_type)
        feats = DocumentFeatures(doc)
        if model_id == "prebuilt-invoice":
            fields, doc_type = map_invoice(feats), "invoice"
        elif model_id == "prebuilt-po":
            fields, doc_type = map_po(feats), "purchaseOrder"
        else:
            fields, doc_type = None, None
        job.result = build_analyze_result(model_id, job.api_version, doc, fields, doc_type)
        job.status = "succeeded"
        log.info("job %s succeeded in %.1fs", job.result_id, time.time() - started)
    except engine_mod.EngineError as e:
        job.status = "failed"
        job.error = {"code": "InvalidRequest", "message": f"OCR engine failed: {e}"}
        log.warning("job %s failed: %s", job.result_id, e)
    except Exception as e:  # noqa: BLE001 — never let a job kill the loop
        job.status = "failed"
        job.error = {"code": "InvalidRequest", "message": f"Processing failed: {e}"}
        log.exception("job %s crashed", job.result_id)
    job.updated = time.time()
