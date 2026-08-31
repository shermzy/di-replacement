"""E2E: drive the gateway exactly like an Azure DI caller would."""
import time
import httpx

BASE = "http://127.0.0.1:8000"
PDF = "sample_invoice.pdf"

with open(PDF, "rb") as f:
    data = f.read()

r = httpx.post(
    f"{BASE}/formrecognizer/documentModels/prebuilt-invoice:analyze?api-version=2023-07-31",
    content=data,
    headers={"Content-Type": "application/pdf", "Ocp-Apim-Subscription-Key": "test"},
)
print("POST status:", r.status_code)
assert r.status_code == 202, r.text
loc = r.headers["Operation-Location"]
print("Operation-Location:", loc)

body = None
for _ in range(240):  # up to 4 min
    rr = httpx.get(loc, headers={"Ocp-Apim-Subscription-Key": "test"})
    body = rr.json()
    if body["status"] != "running":
        break
    time.sleep(2)

print("Final status:", body["status"])
if body["status"] != "succeeded":
    print("FAILED:", body.get("error"))
    raise SystemExit(1)

ar = body["analyzeResult"]
doc = ar["documents"][0]
print("docType:", doc["docType"], "| confidence:", doc["confidence"])
for k, v in doc["fields"].items():
    if k == "Items":
        items = v["valueArray"]
        print(f"{k}: {len(items)} rows")
        for it in items:
            obj = it["valueObject"]
            parts = {kk: (vv.get("valueString") or vv.get("valueNumber")) for kk, vv in obj.items()}
            print("   ", parts)
    else:
        val = v.get("valueString") or v.get("valueNumber") or v.get("valueDate")
        print(f"{k:14s} = {val!r} (conf {v['confidence']})")

# assertions on the real OCR path
f = doc["fields"]
assert f["InvoiceId"]["valueString"] == "INV-2024-001", f["InvoiceId"]
assert f["InvoiceTotal"]["valueNumber"] == 1090.0, f["InvoiceTotal"]
items = f["Items"]["valueArray"]
assert len(items) == 2, f"expected 2 items, got {len(items)}"
assert items[0]["valueObject"]["Description"]["valueString"] == "Widget A"
assert items[0]["valueObject"]["Amount"]["valueNumber"] == 500.0
print("\nE2E OK: real OCR -> Azure-shaped response verified")
