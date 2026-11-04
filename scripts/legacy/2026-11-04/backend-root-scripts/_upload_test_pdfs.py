"""Upload selected test PDFs to the test KB with multimodal chunking."""
from pathlib import Path
import httpx

API_BASE = "http://127.0.0.1:58080"
KB_ID = "kb-96eca485"
PDF_DIR = Path(r"E:\Desktop\agent\data\pdfs")

FILES = [
    "mcu/stm32f103c8_datasheet.pdf",
    "mcu/esp32-s3_datasheet.pdf",
    "sensors/mpu6050_datasheet.pdf",
    "sensors/bme280_datasheet.pdf",
    "interface/ch340g_datasheet.pdf",
]

results = []
for rel in FILES:
    p = PDF_DIR / rel
    print(f"\nUploading {rel} ({p.stat().st_size / 1024:.1f} KB)...")
    with open(p, "rb") as f:
        r = httpx.post(
            f"{API_BASE}/api/kb/upload",
            data={"kb_id": KB_ID, "chunk_method": "multimodal", "small_chunk_size": "800"},
            files={"file": (p.name, f, "application/pdf")},
            timeout=120,
        )
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text}
    print(f"  status={r.status_code}, resp={data}")
    results.append({"file": rel, "status": r.status_code, "response": data})

print("\n=== Summary ===")
for r in results:
    print(r["file"], r["status"], r["response"].get("success") if isinstance(r["response"], dict) else "?")
