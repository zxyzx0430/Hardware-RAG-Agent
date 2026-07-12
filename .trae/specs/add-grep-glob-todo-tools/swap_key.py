"""Swap the openai provider's encrypted_key with deepseek's so the default
provider resolves to a valid key. Both providers share the same base_url."""
import json
from pathlib import Path

store_path = Path(r"e:\Desktop\agent\backend\app\db\keys_store.json")
data = json.loads(store_path.read_text(encoding="utf-8"))

ds_key = data["providers"]["deepseek"]["encrypted_key"]
ds_base = data["providers"]["deepseek"]["base_url"]
old_openai_key = data["providers"]["openai"]["encrypted_key"]

data["providers"]["openai"]["encrypted_key"] = ds_key
data["providers"]["openai"]["base_url"] = ds_base
data["providers"]["openai"]["updated_at"] = "2026-07-02T15:55:54.335853"

store_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
print("Swapped openai encrypted_key with deepseek's")
print(f"  old openai key (first 30): {old_openai_key[:30]}...")
print(f"  new openai key (first 30): {ds_key[:30]}...")
