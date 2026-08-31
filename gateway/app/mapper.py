"""Map Docling document JSON -> Azure Document Intelligence analyzeResult.

Rules-based MVP: key-value labels + table heuristics. See DESIGN.md section 5.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

# ---------------------------------------------------------------- parsing


def normalize_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def parse_amount(s: str) -> Optional[float]:
    """Parse currency amounts incl. "$1,234.56", "1.234,56", "S$1,090.00"."""
    if not s:
        return None
    t = s.strip()
    if t.startswith("(") and t.endswith(")"):
        t = "-" + t[1:-1]
    t = re.sub(r"[^\d.,\-]", "", t)
    if not t:
        return None
    neg = t.startswith("-")
    t = t.lstrip("-")
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):  # 1.234,56
            t = t.replace(".", "").replace(",", ".")
        else:  # 1,234.56
            t = t.replace(",", "")
    elif "," in t:
        if re.fullmatch(r"\d{1,3}(,\d{3})+", t):  # 1,234
            t = t.replace(",", "")
        else:  # 123,45
            t = t.replace(",", ".")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


_DATE_FORMATS = [
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d %b %Y",
    "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d %b %y", "%d/%m/%y",
]


def parse_date(s: str) -> Optional[str]:
    """Return ISO date 'YYYY-MM-DD' or None."""
    if not s:
        return None
    t = re.sub(r"\s+", " ", s.strip().rstrip("."))
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- field builders


def f_string(value: str, content: str, confidence: float) -> dict:
    return {"type": "string", "valueString": value, "content": content,
            "confidence": confidence, "boundingRegions": [], "spans": []}


def f_number(value: float, content: str, confidence: float) -> dict:
    return {"type": "number", "valueNumber": value, "content": content,
            "confidence": confidence, "boundingRegions": [], "spans": []}


def f_date(value: str, content: str, confidence: float) -> dict:
    return {"type": "date", "valueDate": value, "content": content,
            "confidence": confidence, "boundingRegions": [], "spans": []}


def f_array(items: list) -> dict:
    return {"type": "array", "valueArray": items, "content": "",
            "confidence": 0.0, "boundingRegions": [], "spans": []}


def f_object(fields: dict) -> dict:
    return {"type": "object", "valueObject": fields, "content": "",
            "confidence": 0.0, "boundingRegions": [], "spans": []}


# ---------------------------------------------------------------- doc features


class DocumentFeatures:
    """Extract texts / key-value pairs / tables from a Docling JSON doc."""

    def __init__(self, doc: dict):
        self.doc = doc
        self.texts: list[str] = [
            t.get("text", "") for t in (doc.get("texts") or [])
        ]
        self.kv: dict[str, str] = {}
        self.kv_ordered: list[tuple[str, str]] = []
        self._build_kv()
        self.tables: list[list[list[str]]] = self._build_tables()

    def _resolve(self, node: Any) -> str:
        if isinstance(node, str):
            return node
        if isinstance(node, dict):
            if node.get("text") is not None:
                return str(node["text"])
            ref = node.get("self_ref") or node.get("$ref") or ""
            m = re.search(r"#/texts/(\d+)$", ref)
            if m:
                idx = int(m.group(1))
                if 0 <= idx < len(self.texts):
                    return self.texts[idx]
        return ""

    def _build_kv(self) -> None:
        for kv in self.doc.get("key_value_items") or []:
            key = self._resolve(kv.get("key"))
            val = self._resolve(kv.get("value"))
            if key and val:
                self.kv[normalize_key(key)] = val
                self.kv_ordered.append((key, val))

    def _build_tables(self) -> list[list[list[str]]]:
        out = []
        for tbl in self.doc.get("tables") or []:
            data = tbl.get("data")
            if not data:
                continue
            if isinstance(data, dict) and isinstance(data.get("table_cells"), list):
                # Docling v1/v1.31 serializes TableData as a cell list with
                # zero-based offsets, rather than as a 2-D matrix.
                rows = int(data.get("num_rows") or 0)
                cols = int(data.get("num_cols") or 0)
                cells = data["table_cells"]
                if not rows:
                    rows = max((int(c.get("end_row_offset_idx") or
                                    c.get("start_row_offset_idx", 0)) for c in cells), default=-1) + 1
                if not cols:
                    cols = max((int(c.get("end_col_offset_idx") or
                                    c.get("start_col_offset_idx", 0)) for c in cells), default=-1) + 1
                grid = [["" for _ in range(cols)] for _ in range(rows)]
                for cell in cells:
                    r = int(cell.get("start_row_offset_idx", 0))
                    c = int(cell.get("start_col_offset_idx", 0))
                    if 0 <= r < rows and 0 <= c < cols:
                        grid[r][c] = self._resolve(cell)
                out.append(grid)
                continue
            if isinstance(data, list):
                grid = [[self._resolve(cell) for cell in row] for row in data]
                out.append(grid)
        return out

    def kv_lookup(self, labels: list[str]) -> Optional[tuple[str, str]]:
        for label in labels:
            v = self.kv.get(normalize_key(label))
            if v:
                return label, v
        # fuzzy: label appears inside the key (e.g. "Invoice Number:")
        for key, val in self.kv_ordered:
            nk = normalize_key(key)
            for label in labels:
                nl = normalize_key(label)
                if nl and (nk == nl or nk.startswith(nl) or nl in nk):
                    return key, val
        return None

    def line_lookup(self, labels: list[str]) -> Optional[str]:
        """Find 'Label[:] value' inside text blocks, cutting at the next label.

        Handles both one-field-per-line and the engine's merged blocks like
        'Invoice No: X Invoice Date: Y ...' (which is what Docling actually emits).
        """
        for label in labels:
            lab = re.escape(label)
            pat = re.compile(
                r"(?:^|\s)" + lab + LABEL_PREFIX_RE + r"(.+?)(?=\s*$|"
                + NEXT_LABEL_RE.pattern + r")", re.IGNORECASE | re.DOTALL)
            for t in self.texts:
                m = pat.search(t)
                if m:
                    return m.group(1).strip()
        return None

    def text_find(self, pattern: str) -> Optional[str]:
        for t in self.texts:
            m = re.search(pattern, t, re.IGNORECASE)
            if m and m.group(1):
                return m.group(1)
        return None

    @property
    def full_text(self) -> str:
        return "\n".join(self.texts)


# ---------------------------------------------------------------- label tables

INVOICE_LABELS = {
    "InvoiceId": ["invoice no", "invoice number", "invoice id", "invoice #", "inv no"],
    "InvoiceDate": ["invoice date", "date", "issued date"],
    "DueDate": ["due date", "payment due date", "payment due"],
    "VendorName": ["vendor", "vendor name", "supplier", "supplier name", "from"],
    "VendorAddress": ["vendor address", "supplier address"],
    "CustomerName": ["bill to", "customer", "customer name", "sold to"],
    "CustomerAddress": ["bill to address", "customer address"],
    "PurchaseOrder": ["po number", "purchase order", "po no", "order no", "customer po", "your ref"],
    "SubTotal": ["subtotal", "sub total", "net total", "net amount"],
    "TotalTax": ["tax", "gst", "vat", "total tax", "tax amount", "gst 9%"],
    "InvoiceTotal": ["total", "invoice total", "grand total", "total amount", "total due", "amount payable"],
    "AmountDue": ["amount due", "balance due"],
    "CurrencyCode": ["currency", "currency code"],
    "PaymentTerm": ["payment terms", "terms"],
}

PO_LABELS = {
    "PurchaseOrderNumber": ["po number", "purchase order", "po no", "purchase order no", "order number"],
    "VendorName": ["vendor", "vendor name", "supplier", "supplier name"],
    "VendorAddress": ["vendor address", "supplier address"],
    "ShipToName": ["ship to", "ship to name", "deliver to", "delivery to"],
    "ShipToAddress": ["ship to address", "delivery address"],
    "BillToName": ["bill to", "bill to name"],
    "BillToAddress": ["bill to address", "billing address"],
    "OrderDate": ["order date", "date", "po date"],
    "RequestedDeliveryDate": ["requested delivery date", "delivery date", "expected delivery", "required date"],
    "CurrencyCode": ["currency", "currency code"],
    "SubTotal": ["subtotal", "sub total", "net total"],
    "TotalTax": ["tax", "gst", "vat", "total tax"],
    "TotalAmount": ["total", "grand total", "total amount", "amount payable"],
}

_ALL_LABEL_TOKENS: list[str] = []
for _table in (INVOICE_LABELS, PO_LABELS):
    for _labels in _table.values():
        _ALL_LABEL_TOKENS.extend(_labels)
_ALL_LABEL_TOKENS = sorted({re.escape(t) for t in _ALL_LABEL_TOKENS}, key=len, reverse=True)

# matches " <label>(...):" start of a following field inside one text block
NEXT_LABEL_RE = re.compile(
    r"\s+(?:" + "|".join(_ALL_LABEL_TOKENS) + r")\s*(?:\([^)]*\))?\s*[:#]", re.IGNORECASE)
# matches "<label>(...):" at a value position
LABEL_PREFIX_RE = r"\s*(?:\([^)]*\))?\s*[:#]?\s*"

ITEM_HEADERS = {
    "description": ["description", "item", "particulars", "product", "goods", "description of goods", "item description"],
    "quantity": ["qty", "quantity", "units", "unit"],
    "unit_price": ["unit price", "price", "rate", "unit cost"],
    "amount": ["amount", "total", "line total", "net amount", "value"],
    "tax": ["tax", "gst", "vat"],
}


def _item_columns(header: list[str]) -> dict:
    cols: dict[str, Optional[int]] = {"description": None, "quantity": None,
                                     "unit_price": None, "amount": None, "tax": None}
    strength: dict[str, int] = {role: 99 for role in cols}
    for i, h in enumerate(header):
        nh = normalize_key(h)
        if not nh:
            continue
        for role, labels in ITEM_HEADERS.items():
            for idx, lab in enumerate(labels):
                if nh == normalize_key(lab) and idx < strength[role]:
                    cols[role] = i
                    strength[role] = idx
                    break
    return cols


def map_items(doc: DocumentFeatures) -> list[dict]:
    """Pick the most item-table-like table and map its rows to Azure Items."""
    best: Optional[tuple[int, dict, list[list[str]]]] = None
    for t_idx, grid in enumerate(doc.tables):
        if len(grid) < 2:
            continue
        cols = _item_columns(grid[0])
        score = sum(1 for v in cols.values() if v is not None)
        if cols["description"] is not None and cols["amount"] is not None:
            score += 2
        if best is None or score > best[0]:
            best = (score, cols, grid)
    if not best or best[0] < 3:
        return []

    _, cols, grid = best
    items: list[dict] = []
    for row in grid[1:]:
        def cell(i: Optional[int]) -> str:
            return row[i].strip() if i is not None and i < len(row) else ""

        desc = cell(cols["description"])
        if not desc:
            continue
        n_desc = normalize_key(desc)
        _totals = [normalize_key(x) for x in
                   ["subtotal", "sub total", "total", "grand total", "tax", "gst", "balance"]]
        if n_desc in _totals:
            continue
        obj_fields = {"Description": f_string(desc, desc, 0.85)}
        qty = parse_amount(cell(cols["quantity"]))
        if qty is not None:
            obj_fields["Quantity"] = f_number(qty, cell(cols["quantity"]), 0.85)
        price = parse_amount(cell(cols["unit_price"]))
        if price is not None:
            obj_fields["UnitPrice"] = f_number(price, cell(cols["unit_price"]), 0.8)
        amount = parse_amount(cell(cols["amount"]))
        if amount is None and qty is not None and price is not None:
            amount = round(qty * price, 2)
        if amount is not None:
            obj_fields["Amount"] = f_number(amount, cell(cols["amount"]) or "", 0.85)
        tax = parse_amount(cell(cols["tax"]))
        if tax is not None:
            obj_fields["Tax"] = f_number(tax, cell(cols["tax"]), 0.7)
        items.append(f_object(obj_fields))
    return items


# ---------------------------------------------------------------- mappers


def _map_scalar(doc: DocumentFeatures, key: str, labels: list[str],
                kind: str, confidence: float) -> dict:
    hit = doc.kv_lookup(labels)
    raw, content = (hit[1], hit[1]) if hit else (None, "")
    if raw is None:
        raw = doc.line_lookup(labels)
        content = raw or ""
    if raw is None:
        return f_string("", "", 0.0)
    raw = raw.strip()
    if kind == "number":
        v = parse_amount(raw)
        if v is not None:
            return f_number(v, content, confidence)
        return f_string(raw, content, 0.3)
    if kind == "date":
        v = parse_date(raw)
        if v is not None:
            return f_date(v, content, confidence)
        return f_string(raw, content, 0.3)
    return f_string(raw, content, confidence)


def map_invoice(doc: DocumentFeatures) -> dict:
    fields: dict[str, dict] = {}
    for key, labels in INVOICE_LABELS.items():
        kind = "number" if key in ("SubTotal", "TotalTax", "InvoiceTotal", "AmountDue") \
            else "date" if key in ("InvoiceDate", "DueDate") else "string"
        f = _map_scalar(doc, key, labels, kind, 0.9)
        if f.get("valueString") or f.get("valueNumber") is not None or f.get("valueDate"):
            fields[key] = f
    # vendor name fallback: first text line is usually the company header
    if "VendorName" not in fields and doc.texts:
        head = doc.texts[0].strip()
        if head and len(head) < 80:
            fields["VendorName"] = f_string(head, head, 0.5)
    items = map_items(doc)
    if items:
        fields["Items"] = f_array(items)
    return fields


def map_po(doc: DocumentFeatures) -> dict:
    fields: dict[str, dict] = {}
    for key, labels in PO_LABELS.items():
        kind = "number" if key in ("SubTotal", "TotalTax", "TotalAmount") \
            else "date" if key in ("OrderDate", "RequestedDeliveryDate") else "string"
        f = _map_scalar(doc, key, labels, kind, 0.9)
        if f.get("valueString") or f.get("valueNumber") is not None or f.get("valueDate"):
            fields[key] = f
    items = map_items(doc)
    if items:
        fields["Items"] = f_array(items)
    return fields


# ---------------------------------------------------------------- analyze result


def build_pages(doc: DocumentFeatures) -> list[dict]:
    pages = []
    raw_pages = doc.doc.get("pages") or {}
    for key in sorted(raw_pages, key=lambda k: int(k) if str(k).isdigit() else 0):
        p = raw_pages[key]
        size = p.get("size") or {}
        w = float(size.get("width", 612)) / 72.0
        h = float(size.get("height", 792)) / 72.0
        pages.append({
            "pageNumber": int(p.get("page_no", key)),
            "width": round(w, 3),
            "height": round(h, 3),
            "unit": "inch",
        })
    return pages or [{"pageNumber": 1, "width": 8.5, "height": 11.0, "unit": "inch"}]


def build_paragraphs(doc: DocumentFeatures) -> list[dict]:
    paragraphs = []
    offset = 0
    for text in doc.texts:
        if text:
            paragraphs.append({
                "content": text,
                "boundingRegions": [],
                "spans": [{"offset": offset, "length": len(text)}],
            })
        offset += len(text) + 1  # full_text joins text elements with newline
    return paragraphs


def _span_for(content: str, needle: str) -> list[dict]:
    idx = content.find(needle)
    if idx < 0:
        return []
    return [{"offset": idx, "length": len(needle)}]


def build_azure_tables(doc: DocumentFeatures) -> list[dict]:
    out = []
    for grid in doc.tables:
        cells = []
        for r, row in enumerate(grid):
            for c, text in enumerate(row):
                cells.append({
                    "kind": "content",
                    "rowIndex": r, "columnIndex": c,
                    "rowSpan": 1, "columnSpan": 1,
                    "content": text,
                    "boundingRegions": [],
                    "spans": _span_for(doc.full_text, text) if text else [],
                })
        out.append({"rowCount": len(grid),
                    "columnCount": max((len(r) for r in grid), default=0),
                    "cells": cells})
    return out


def build_kv_pairs(doc: DocumentFeatures) -> list[dict]:
    return [{
        "key": {"content": k, "boundingRegions": [], "spans": _span_for(doc.full_text, k)},
        "value": {"content": v, "boundingRegions": [], "spans": _span_for(doc.full_text, v)},
        "confidence": 0.8,
    } for k, v in doc.kv_ordered]


def build_analyze_result(model_id: str, api_version: str, doc: dict,
                         fields: Optional[dict] = None,
                         doc_type: Optional[str] = None) -> dict:
    feats = DocumentFeatures(doc)
    result: dict[str, Any] = {
        "apiVersion": api_version,
        "modelId": model_id,
        "stringIndexType": "textElements",
        "content": feats.full_text,
        "pages": build_pages(feats),
        "paragraphs": build_paragraphs(feats),
        "tables": build_azure_tables(feats),
        "keyValuePairs": build_kv_pairs(feats),
    }
    if fields is not None:
        confs = [f["confidence"] for f in fields.values() if isinstance(f, dict)]
        result["documents"] = [{
            "docType": doc_type or "document",
            "confidence": round(sum(confs) / len(confs), 2) if confs else 0.0,
            "fields": fields,
        }]
    return result
