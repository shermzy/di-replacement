"""Capture reference Azure DI responses for golden-set comparison.

Usage:
  DI_ENDPOINT=https://....cognitiveservices.azure.com/ DI_KEY=... \
    python capture_azure.py path/to/doc.pdf [more files...]

Writes JSON responses to ../azure-reference/<basename>.json.
"""
import base64
import json
import os
import sys
from pathlib import Path

import httpx

MODELS = ["prebuilt-invoice", "prebuilt-layout"]

OUT = Path(__file__).resolve().parents[1] / "azure-reference"
OUT.mkdir(exist_ok=True)


def capture(endpoint: str, key: str, path: Path, model: str):
    data = path.read_bytes()
    headers = {"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/pdf"}
    url = f"{endpoint}/formrecognizer/documentModels/{model}:analyze?api-version=2023-07-31"
    with httpx.Client(timeout=60) as client:
        r = client.post(url, headers=headers, content=data)
        r.raise_for_status()
        loc = r.headers["Operation-Location"]
        while True:
            res = client.get(loc, headers={"Ocp-Apim-Subscription-Key": key}).json()
            if res["status"] != "running":
                break
            import time
            time.sleep(1)
    out = OUT / f"{path.stem}__{model}.json"
    out.write_text(json.dumps(res, indent=2))
    print(f"saved {out}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    endpoint = os.environ["DI_ENDPOINT"].rstrip("/")
    key = os.environ["DI_KEY"]
    for p in sys.argv[1:]:
        path = Path(p)
        for model in MODELS:
            capture(endpoint, key, path, model)


if __name__ == "__main__":
    main()
