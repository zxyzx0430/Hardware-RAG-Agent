"""List available models from configured LLM_BASE_URL."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import httpx

BASE_URL = os.getenv("LLM_BASE_URL", "")
API_KEY = os.getenv("LLM_API_KEY", "")


def main():
    if not BASE_URL:
        print("错误：请设置 LLM_BASE_URL 环境变量")
        sys.exit(1)
    headers = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}
    try:
        r = httpx.get(f"{BASE_URL}/models", headers=headers, timeout=30)
        r.raise_for_status()
        models = r.json().get("data", [])
        print(f"Available models ({len(models)}):")
        for m in models:
            print(f"  {m.get('id')}")
    except Exception as e:
        print(f"Failed to list models: {e}")


if __name__ == "__main__":
    main()
