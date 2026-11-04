"""Re-index 01-stm32-gpio.md in builtin-001 with hybrid chunking."""
from pathlib import Path

import httpx

API_BASE = "http://127.0.0.1:58080"
KB_ID = "builtin-001"
FILE_PATH = Path(r"E:\Desktop\agent\data\test_docs\01-stm32-gpio.md")


def find_doc_id() -> str | None:
    r = httpx.get(f"{API_BASE}/api/kb/list?kb_id={KB_ID}", timeout=60)
    r.raise_for_status()
    docs = r.json().get("data", {}).get("documents", [])
    for d in docs:
        if d.get("title") == FILE_PATH.name:
            return d["doc_id"]
    return None


def delete_doc(doc_id: str) -> None:
    r = httpx.post(f"{API_BASE}/api/kb/delete", json={"doc_id": doc_id}, timeout=120)
    r.raise_for_status()
    print(f"Deleted doc {doc_id}: {r.json()}")


def upload_doc() -> dict:
    with FILE_PATH.open("rb") as f:
        files = {"file": (FILE_PATH.name, f, "text/markdown")}
        data = {"kb_id": KB_ID, "chunk_method": "hybrid"}
        r = httpx.post(f"{API_BASE}/api/kb/upload", files=files, data=data, timeout=120)
    r.raise_for_status()
    return r.json()


def main():
    old_doc_id = find_doc_id()
    if old_doc_id:
        print(f"Found existing doc {old_doc_id}, deleting...")
        delete_doc(old_doc_id)
    else:
        print("No existing doc found.")

    print("Uploading fresh copy with hybrid chunking...")
    result = upload_doc()
    print(f"Upload response: {result}")
    if result.get("success"):
        print("Upload accepted; indexing in background.")
    else:
        print(f"Upload failed: {result.get('error')}")


if __name__ == "__main__":
    main()
