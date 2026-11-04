"""Debug: inspect how re.split extracts code blocks from backend-api-spec.md."""
import re
from pathlib import Path

text = Path("data/uploads/b2157af5-7f63-4700-909d-5506f131f7f7.md").read_text(encoding="utf-8")

parts = re.split(r"(```[\s\S]*?```)", text)
print(f"Total parts: {len(parts)}")
print()
for i, p in enumerate(parts):
    is_code = p.startswith("```") and p.endswith("```")
    tag = "[CODE]" if is_code else "[TEXT]"
    preview = p[:200].replace("\n", " / ")
    print(f"  [{i:2d}] {tag} len={len(p):<5} {preview}")
    if is_code:
        # Show last 100 chars to verify the code block ends correctly
        tail = p[-100:].replace("\n", " / ")
        print(f"         tail: {tail}")
