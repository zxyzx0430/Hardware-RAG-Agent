"""Check pinout-related terms in chunks for mpu6050 and stm32f103."""
import httpx
import re

API_BASE = "http://127.0.0.1:58080"

DOCS = {
    "mpu6050": "9c86944b-c009-4cbd-914a-e8fc7bba703b",
    "stm32f103c8": "8f642c64-855f-4041-9af1-7b4818169b3f",
    "bme280": "4998bf62-e2a0-403c-add5-31ee3b7fedd6",
    "esp32s3": "f0c9b03b-1703-4e01-9637-211a4cd39035",
}

TERMS = ["pinout", "pin out", "pin configuration", "pin assignment", "pin function", "pin description", "package pin", "pin diagram"]

for name, doc_id in DOCS.items():
    r = httpx.get(f"{API_BASE}/api/kb/documents/{doc_id}/chunks", timeout=60)
    chunks = r.json()["data"]["chunks"]
    all_text = "\n".join(c["content"] for c in chunks).lower()
    found = [t for t in TERMS if t in all_text]
    print(f"{name}: {len(chunks)} chunks, found terms: {found}")
