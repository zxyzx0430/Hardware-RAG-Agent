"""Restart backend and re-upload the GPIO extract PDF."""
import subprocess
import time
import httpx
import os

os.chdir(r"E:\Desktop\agent\backend")

# Kill existing
subprocess.run(
    ["powershell", "-Command",
     "Get-NetTCPConnection -LocalPort 58080 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique"],
    capture_output=True, text=True
)

result = subprocess.run(
    ["powershell", "-Command",
     "Stop-Process -Id (Get-NetTCPConnection -LocalPort 58080 -ErrorAction SilentlyContinue).OwningProcess -Force -ErrorAction SilentlyContinue"],
    capture_output=True, text=True
)
print("Killed old backend")
time.sleep(3)

# Start new
proc = subprocess.Popen(
    ["python", "main.py", "--web", "--port", "58080"],
    cwd=r"E:\Desktop\agent\backend",
    creationflags=0x08000000,  # CREATE_NO_WINDOW
)
print(f"Started backend PID {proc.pid}")

# Wait for startup
for i in range(15):
    time.sleep(2)
    try:
        r = httpx.get("http://127.0.0.1:58080/api/health", timeout=5)
        print(f"Health: {r.status_code} after {(i+1)*2}s")
        break
    except Exception:
        print(f"Waiting... {(i+1)*2}s")

# Re-upload the extract PDF
print("\nRe-uploading stm32f4_gpio_exti_extract.pdf...")
pdf_path = r"E:\Desktop\agent\data\pdfs\mcu\stm32f4_gpio_exti_extract.pdf"
with open(pdf_path, "rb") as f:
    r = httpx.post(
        "http://127.0.0.1:58080/api/kb/upload",
        data={"kb_id": "kb-96eca485", "chunk_method": "multimodal", "small_chunk_size": "2000"},
        files={"file": ("stm32f4_gpio_exti_extract.pdf", f, "application/pdf")},
        timeout=120,
    )
print(f"Upload: {r.status_code}")
resp = r.json()
print(resp)
doc_id = resp.get("data", {}).get("doc_id", "")
print(f"doc_id: {doc_id}")
