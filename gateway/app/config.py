"""Gateway configuration (env-driven)."""
import os

API_KEY = os.environ.get("DI_API_KEY", "")
DOCLING_URL = os.environ.get("DOCLING_URL", "http://docling:5001")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "25"))
ENGINE_TIMEOUT = float(os.environ.get("ENGINE_TIMEOUT", "300"))
SUPPORTED_API_VERSIONS = {"2023-07-31", "2024-11-30"}
