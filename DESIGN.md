# DocIntel-Local — Azure Document Intelligence replacement

Self-hosted service on Coolify, CPU-only, drop-in compatible with the Azure Document
Intelligence (DI) REST API for the models we actually use.

## 1. Scope

| In | Out (for now) |
| --- | --- |
| Invoices: key fields + line-item tables | Handwriting, IDs, receipts |
| Purchase orders: same | Custom trained models |
| API drop-in for existing n8n callers | Azure SDK blob/label training |
| PDF + image input (jpg/png/tiff) | docx/xlsx/html rendering |

Supported modelIds on day 1:
- `prebuilt-invoice` (full field set, see §4)
- `prebuilt-po` (our own field schema — Azure has no PO model; same wire format)
- `prebuilt-layout`, `prebuilt-read` (cheap passthrough from engine output)

## 2. Architecture

```
 caller (n8n)
     |
     v
+-------------+      POST {gateway}/formrecognizer/documentModels/{id}:analyze
|   gateway   |  ->  202 + Operation-Location header (api-version=2023-07-31)
|  FastAPI    |      GET  .../analyzeResults/{resultId}  (status, then result)
+-----+-------+
      | in-memory job store (single-process async worker)
      v
+-------------+      /v1/convert/file (docling-serve v1)
|   engine    |  ->  Docling JSON: OCR text + layout + tables
|  docling    |
+-----+-------+
      |
      v
+-------------+      maps engine JSON -> Azure DI field schema
|  mapper     |      rules/heuristics + optional LLM refinement
+-------------+
```

Two containers on Coolify:
1. `gateway` — our FastAPI app (custom Dockerfile)
2. `docling` — `ghcr.io/docling-project/docling-serve-cpu:latest` (unmodified)

The gateway uses Docling's stable v1 API. It keeps the Azure-compatible analysis
job in an in-memory store, so deploy it as one gateway worker; a restart discards
in-flight/results. Add Redis/Postgres before running long-lived or multi-worker
production workloads.

Both CPU-only. ~2–4 GB RAM; a 2-page invoice ≈ 5–15 s cold, ~3–8 s warm.

## 3. Drop-in contract (what "drop-in" must mean)

Request:
- `POST /formrecognizer/documentModels/{modelId}:analyze?api-version=2023-07-31`
- Header `Ocp-Apim-Subscription-Key` — accepted, value checked against env `DI_API_KEY` (empty = development/no authentication; set it in Coolify)
- Body either raw file bytes (`Content-Type: application/pdf|image/*`) or JSON `{"urlSource": "https://..."}`
- Optional `features=ocrHighResolution` ignored gracefully

Response flow (must match Azure exactly):
1. `202 Accepted` + `Operation-Location: .../analyzeResults/{resultId}`
2. Poll returns `{"status": "running"}` then
   `{"status": "succeeded", "analyzeResult": {...}}`
3. Errors: `400` invalid request, `404` unknown model/result — same shape as Azure's
   `error` object (`code`, `message`).

Result body carries the standard objects currently implemented: `content`, `pages`,
`paragraphs`, `tables` (cells with spans/boundingRegions), `keyValuePairs`, and
`documents[].fields`. The layout/read models are intentionally a useful subset,
not a complete Azure replacement for words, selection marks, figures, or custom
models.

## 4. Invoice field schema (matches Azure prebuilt-invoice)

Top-level: CustomerName, CustomerId, PurchaseOrder, InvoiceId, InvoiceDate,
DueDate, VendorName, VendorAddress, VendorAddressRecipient, CustomerAddress,
CustomerAddressRecipient, BillingAddress, BillingAddressRecipient, ShippingAddress,
ShippingAddressRecipient, SubTotal, TotalTax, InvoiceTotal, AmountDue,
PreviousUnpaidBalance, PaymentTerm, CurrencyCode, RemittanceAddress,
RemittanceAddressRecipient, ServiceAddress, ServiceAddressRecipient, TaxDetails.

Items[] (line items): Description, Quantity, Unit, UnitPrice, Amount, Tax, TaxRate,
ProductCode, Date.

Each recognized field: `{type, valueString|valueNumber|valueDate|valueArray, content, confidence, boundingRegions, spans}`.
Absent fields are omitted in this MVP (rather than synthesizing null values), so
n8n validation must treat missing fields as review-required.

PO schema (ours): PurchaseOrderNumber, VendorName, VendorAddress, ShipToName,
ShipToAddress, BillToName, BillToAddress, OrderDate, RequestedDeliveryDate,
CurrencyCode, SubTotal, TotalTax, TotalAmount, Items[].

## 5. Mapper strategy (the hard 20%)

Docling gives text + table structure, not semantics. Two passes:

1. **Rule pass** (always): key candidates from docling `key_value_items`/labels;
   currency amounts matched against label dictionaries ("total", "TOTAL AMOUNT",
   "GRAND TOTAL", "subtotal", "GST", "tax", "amount due"); dates via locale-aware
   parsing; `Items` = largest table filtered to rows whose Description cell is
   non-numeric + has Amount/UnitPrice siblings.
2. **LLM pass** (optional, disabled by default): send condensed JSON to any
   OpenAI-compatible endpoint for field/value JSON. Flag `use_llm` per request or
   env. Pure CPU hosts shouldn't run local LLMs — use an existing API key if wanted.

Confidence: rules emit 0.85/0.5 tiers; LLM emits model-provided values. Everything
below `MIN_CONFIDENCE` (default 0.4) still ships, just low — Azure never hard-fails
a field. Fail-closed is a consumer concern (n8n validation), not the OCR service's.

## 6. Testing (before it counts)

- Golden set: 20 invoices + 10 POs we hold locally, PDF + photo scans.
- Compare against real Azure DI responses captured for the same files (save them
  NOW while the Azure resource still exists — one script, 30 files).
- Acceptance: field name/type match 100%; value match ≥ 90% on key fields
  (VendorName, InvoiceDate, InvoiceTotal, Items.Description/Quantity/Amount);
  tables cell-for-cell ≥ 85%.
- Per-request latency under 30 s p95 on CPU host.
- Local smoke test verified against Docling Serve v1.31.0 with a real generated
  invoice: 202 Accepted → poll → invoice fields + 2 line items.

## 7. Open questions (decide one at a time)

- [ ] Engine: Docling alone, or Docling + PaddleOCR fallback for rotated/photo scans?
- [ ] LLM pass: leave disabled, or wire the existing Azure OpenAI key for hard docs?
- [ ] PO model: our schema OK, or does an upstream system expect another shape?
- [ ] Where does the repo live? (Coolify hosts it straight from git)
