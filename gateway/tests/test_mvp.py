"""Unit tests for mapper + API contract."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, jobs, main
from app.mapper import DocumentFeatures, build_analyze_result, map_invoice, map_po, parse_amount, parse_date

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------- fixtures

INVOICE_DOC = {
    "name": "inv-001.pdf",
    "pages": {"1": {"page_no": 1, "size": {"width": 595.0, "height": 842.0}}},
    "texts": [
        {"text": "ACME SUPPLIES PTE LTD"},
        {"text": "Invoice No: INV-2024-001"},
        {"text": "Invoice Date: 12/03/2024"},
        {"text": "Due Date: 26/03/2024"},
        {"text": "PO Number: PO-8877"},
        {"text": "Bill To: HiddenXP Pte Ltd"},
        {"text": "Currency: SGD"},
        {"text": "Subtotal: 1,000.00"},
        {"text": "GST (9%): 90.00"},
        {"text": "Total: SGD 1,090.00"},
        {"text": "Amount Due: 1,090.00"},
    ],
    "key_value_items": [
        {"key": {"text": "Invoice No"}, "value": {"text": "INV-2024-001"}},
        {"key": {"text": "Invoice Date"}, "value": {"text": "12/03/2024"}},
        {"key": {"text": "Due Date"}, "value": {"text": "26/03/2024"}},
        {"key": {"text": "Vendor"}, "value": {"text": "ACME SUPPLIES PTE LTD"}},
        {"key": {"text": "Total"}, "value": {"text": "SGD 1,090.00"}},
    ],
    "tables": [
        {
            "data": [
                [{"text": "Item"}, {"text": "Description"}, {"text": "Qty"}, {"text": "Unit Price"}, {"text": "Amount"}],
                [{"text": "1"}, {"text": "Widget A"}, {"text": "10"}, {"text": "50.00"}, {"text": "500.00"}],
                [{"text": "2"}, {"text": "Widget B"}, {"text": "5"}, {"text": "100.00"}, {"text": "500.00"}],
                [{"text": ""}, {"text": "Total"}, {"text": ""}, {"text": ""}, {"text": "1,000.00"}],
            ]
        }
    ],
}

PO_DOC = {
    "name": "po-001.pdf",
    "pages": {"1": {"page_no": 1, "size": {"width": 595.0, "height": 842.0}}},
    "texts": [
        {"text": "Purchase Order: PO-2024-555"},
        {"text": "Vendor: Widgets R Us"},
        {"text": "Order Date: 01/04/2024"},
        {"text": "Requested Delivery Date: 30/04/2024"},
        {"text": "Subtotal: 2,500.00"},
        {"text": "GST: 225.00"},
        {"text": "Total: 2,725.00"},
    ],
    "key_value_items": [
        {"key": {"text": "Purchase Order"}, "value": {"text": "PO-2024-555"}},
        {"key": {"text": "Vendor"}, "value": {"text": "Widgets R Us"}},
        {"key": {"text": "Total"}, "value": {"text": "2,725.00"}},
    ],
    "tables": [
        {
            "data": [
                [{"text": "Description"}, {"text": "Qty"}, {"text": "Unit Price"}, {"text": "Amount"}],
                [{"text": "Gear Assembly"}, {"text": "20"}, {"text": "125.00"}, {"text": "2,500.00"}],
            ]
        }
    ],
}


# ---------------------------------------------------------------- parser tests


@pytest.mark.parametrize("raw,expected", [
    ("1,090.00", 1090.0),
    ("SGD 1,090.00", 1090.0),
    ("$1,234.56", 1234.56),
    ("1.234,56", 1234.56),
    ("(1,000.00)", -1000.0),
    ("500", 500.0),
    ("abc", None),
    ("", None),
])
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("12/03/2024", "2024-03-12"),
    ("26/03/2024", "2024-03-26"),
    ("2024-03-12", "2024-03-12"),
    ("12 Mar 2024", "2024-03-12"),
    ("Mar 12, 2024", "2024-03-12"),
    ("nonsense", None),
])
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


# ---------------------------------------------------------------- mapper tests


def test_invoice_fields():
    feats = DocumentFeatures(INVOICE_DOC)
    fields = map_invoice(feats)
    assert fields["InvoiceId"]["valueString"] == "INV-2024-001"
    assert fields["InvoiceDate"]["valueDate"] == "2024-03-12"
    assert fields["DueDate"]["valueDate"] == "2024-03-26"
    assert fields["VendorName"]["valueString"] == "ACME SUPPLIES PTE LTD"
    assert fields["InvoiceTotal"]["valueNumber"] == 1090.0
    assert fields["SubTotal"]["valueNumber"] == 1000.0
    assert fields["PurchaseOrder"]["valueString"] == "PO-8877"


def test_invoice_items():
    feats = DocumentFeatures(INVOICE_DOC)
    fields = map_invoice(feats)
    items = fields["Items"]["valueArray"]
    assert len(items) == 2  # "Total" row filtered out
    first = items[0]["valueObject"]
    assert first["Description"]["valueString"] == "Widget A"
    assert first["Quantity"]["valueNumber"] == 10.0
    assert first["UnitPrice"]["valueNumber"] == 50.0
    assert first["Amount"]["valueNumber"] == 500.0


def test_po_fields():
    feats = DocumentFeatures(PO_DOC)
    fields = map_po(feats)
    assert fields["PurchaseOrderNumber"]["valueString"] == "PO-2024-555"
    assert fields["VendorName"]["valueString"] == "Widgets R Us"
    assert fields["OrderDate"]["valueDate"] == "2024-04-01"
    assert fields["TotalAmount"]["valueNumber"] == 2725.0
    items = fields["Items"]["valueArray"]
    assert items[0]["valueObject"]["Amount"]["valueNumber"] == 2500.0


def test_analyze_result_shape():
    feats = DocumentFeatures(INVOICE_DOC)
    result = build_analyze_result("prebuilt-invoice", "2023-07-31",
                                  INVOICE_DOC, map_invoice(feats), "invoice")
    assert result["apiVersion"] == "2023-07-31"
    assert result["modelId"] == "prebuilt-invoice"
    assert result["stringIndexType"] == "textElements"
    assert "INV-2024-001" in result["content"]
    assert result["documents"][0]["docType"] == "invoice"
    assert len(result["tables"]) == 1
    assert result["tables"][0]["rowCount"] == 4
    assert len(result["keyValuePairs"]) == 5


# ---------------------------------------------------------------- API contract tests


class FakeEngine:
    def __init__(self):
        self.calls = []

    async def convert(self, filename, data, content_type):
        self.calls.append((filename, content_type))
        if filename.endswith(".pdf"):
            return INVOICE_DOC
        return PO_DOC


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "")
    monkeypatch.setattr(main, "engine", FakeEngine())
    return TestClient(main.app)


def test_analyze_and_poll_invoice(client):
    r = client.post(
        "/formrecognizer/documentModels/prebuilt-invoice:analyze?api-version=2023-07-31",
        content=b"%PDF-1.4 fake", headers={"Content-Type": "application/pdf"},
    )
    assert r.status_code == 202
    loc = r.headers["Operation-Location"]
    assert "/formrecognizer/documentModels/prebuilt-invoice/analyzeResults/" in loc

    import time
    for _ in range(50):
        r = client.get(loc)
        if r.json()["status"] == "succeeded":
            break
        time.sleep(0.1)
    body = r.json()
    assert body["status"] == "succeeded"
    fields = body["analyzeResult"]["documents"][0]["fields"]
    assert fields["InvoiceId"]["valueString"] == "INV-2024-001"
    assert fields["InvoiceTotal"]["valueNumber"] == 1090.0


def test_json_urlsource_rejected_fetch(client):
    r = client.post(
        "/formrecognizer/documentModels/prebuilt-invoice:analyze?api-version=2023-07-31",
        json={"urlSource": "http://127.0.0.1:9/nothing.pdf"},
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "InvalidRequest"


def test_unknown_model_404(client):
    r = client.post(
        "/formrecognizer/documentModels/prebuilt-receipt:analyze?api-version=2023-07-31",
        content=b"x", headers={"Content-Type": "application/pdf"},
    )
    assert r.status_code == 404


def test_bad_api_version_400(client):
    r = client.post(
        "/formrecognizer/documentModels/prebuilt-invoice:analyze?api-version=2022-08-31",
        content=b"x", headers={"Content-Type": "application/pdf"},
    )
    assert r.status_code == 400


def test_missing_key_401(monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "sekret")
    monkeypatch.setattr(main, "engine", FakeEngine())
    c = TestClient(main.app)
    r = c.post(
        "/formrecognizer/documentModels/prebuilt-invoice:analyze?api-version=2023-07-31",
        content=b"x", headers={"Content-Type": "application/pdf"},
    )
    assert r.status_code == 401
    r = c.post(
        "/formrecognizer/documentModels/prebuilt-invoice:analyze?api-version=2023-07-31",
        content=b"x",
        headers={"Content-Type": "application/pdf", "Ocp-Apim-Subscription-Key": "sekret"},
    )
    assert r.status_code == 202


def test_unknown_result_404(client):
    r = client.get(
        "/formrecognizer/documentModels/prebuilt-invoice/analyzeResults/nope?api-version=2023-07-31")
    assert r.status_code == 404


def test_layout_has_no_documents(client):
    r = client.post(
        "/formrecognizer/documentModels/prebuilt-layout:analyze?api-version=2023-07-31",
        content=b"%PDF", headers={"Content-Type": "application/pdf"},
    )
    loc = r.headers["Operation-Location"]
    import time
    for _ in range(50):
        rr = client.get(loc)
        if rr.json()["status"] == "succeeded":
            break
        time.sleep(0.1)
    assert "documents" not in rr.json()["analyzeResult"]
    assert "tables" in rr.json()["analyzeResult"]
