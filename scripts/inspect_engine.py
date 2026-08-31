"""Inspect real Docling engine output shape."""
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))
from app.engine import DoclingClient

async def main():
    c = DoclingClient("http://127.0.0.1:5001", 300)
    doc = await c.convert("sample_invoice.pdf", Path("sample_invoice.pdf").read_bytes(), "application/pdf")
    print("top-level keys:", list(doc.keys()))
    print("texts count:", len(doc.get("texts") or []))
    for t in (doc.get("texts") or [])[:3]:
        print("TEXT:", json.dumps(t)[:400])
    print("key_value_items count:", len(doc.get("key_value_items") or []))
    for kv in (doc.get("key_value_items") or [])[:5]:
        print("KV:", json.dumps(kv)[:400])
    print("tables count:", len(doc.get("tables") or []))
    for tb in (doc.get("tables") or [])[:2]:
        print("TABLE keys:", list(tb.keys()))
        print("TABLE data:", json.dumps(tb.get("data"))[:600])
    Path("real_doc.json").write_text(json.dumps(doc, indent=2))

asyncio.run(main())
