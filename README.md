# DocIntel-Local — Azure Document Intelligence replacement (MVP)

Drop-in replacement for the Azure Document Intelligence REST API for the MVP
models (`prebuilt-invoice`, `prebuilt-po`, `prebuilt-layout`, `prebuilt-read`).
Accepts `api-version=2023-07-31` and `2024-11-30`; self-hosted on Coolify, CPU-only.

See `DESIGN.md` for the full design and open questions.

## Quick start (local)

```bash
docker compose up -d --build
# gateway on http://localhost:8000, docling engine on :5001
```

## Quick start (dev, no Docker)

```bash
cd gateway
uv venv && uv pip install -r requirements.txt
DOCLING_URL=http://localhost:5001 uvicorn app.main:app --port 8000
```

## Call it (same contract as Azure)

```bash
# POST the file, get Operation-Location
curl -X POST "http://localhost:8000/formrecognizer/documentModels/prebuilt-invoice:analyze?api-version=2023-07-31" \
  -H "Content-Type: application/pdf" \
  -H "Ocp-Apim-Subscription-Key: any" \
  --data-binary @invoice.pdf -i

# Poll the returned URL until status != running
curl "http://localhost:8000/formrecognizer/documentModels/prebuilt-invoice/analyzeResults/<resultId>"
```

JSON body with `{"urlSource": "https://..."}` also works.

## Coolify deployment

1. Push this repo to a git remote Coolify can read.
2. Coolify → New Resource → **Docker Compose** → point at the repo.
3. Set public port `8000` on the `gateway` service (or put an FQDN on it).
4. Environment (on `gateway`):
   - `DI_API_KEY` — **set this in production**; callers must send it as `Ocp-Apim-Subscription-Key`. Empty means no authentication.
   - `DOCLING_URL` — leave default (`http://docling:5001`).
   - `MAX_UPLOAD_MB` — default 25.
5. Put the gateway behind HTTPS/auth at the Coolify edge. If you enable `urlSource`,
   review the SSRF/network policy before exposing it beyond your private network.
6. First request is slow (model load, ~10-30 s). Leave
   `DOCLING_SERVE_LOAD_MODELS_AT_BOOT=true` if you want no cold start (uses ~2 GB RAM at boot).

## Env vars

| Var | Default | Meaning |
| --- | --- | --- |
| `DI_API_KEY` | `""` | API key check; empty = no check (development only) |
| `DOCLING_URL` | `http://docling:5001` | docling-serve v1 base URL |
| `MAX_UPLOAD_MB` | `25` | request body cap |

## Known MVP limits

- Job results live in memory — a gateway restart forgets in-flight jobs (Azure results also expire; callers re-submit).
- Single uvicorn worker (matches in-memory jobs).
- Layout/read output is a useful subset; it does not yet include Azure words, selection marks, figures, or custom models.
- Field extraction is rules-based (see `DESIGN.md` §5); an optional LLM pass is designed but not wired.
- `urlSource` currently fetches HTTP(S) URLs from the gateway; keep the service private or add an allowlist/SSRF guard before public exposure.

## Tests

```bash
cd gateway
.venv/Scripts/python.exe -m pytest -q
```
