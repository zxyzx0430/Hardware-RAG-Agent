"""Create a test KB for multimodal PDF chunking evaluation."""
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

API_BASE = "http://127.0.0.1:58080"

embedding_api_key = os.getenv("EMBEDDING_API_KEY")
agent_chunker_api_key = os.getenv("LLM_API_KEY")
if not embedding_api_key or not agent_chunker_api_key:
    print("ERROR: set EMBEDDING_API_KEY and LLM_API_KEY in backend/.env", file=sys.stderr)
    sys.exit(1)

payload = {
    "name": "test-multimodal-pdf",
    "description": "RAG pipeline eval: multimodal chunking on PDFs with pin diagrams and register tables",
    "chunk_method": "multimodal",
    "small_chunk_size": 800,
    "embedding_model": "text-embedding-v4",
    "embedding_base_url": os.getenv("EMBEDDING_BASE_URL"),
    "embedding_api_key": embedding_api_key,
    "agent_chunker_model": "oc/mimo-v2.5",
    "agent_chunker_base_url": os.getenv("LLM_BASE_URL"),
    "agent_chunker_api_key": agent_chunker_api_key,
    "context_window": 256000,
}

r = httpx.post(f"{API_BASE}/api/kb/collections", json=payload, timeout=60)
print(r.status_code)
print(r.json())
