"""Remove source_pages from PDF golden dataset and move to notes."""
import yaml
from pathlib import Path

ds_path = Path(r"E:\Desktop\agent\backend\tests\rag_eval\golden_dataset_pdf.yaml")

with open(ds_path, "r", encoding="utf-8") as f:
    data = yaml.safe_load(f)

for s in data["samples"]:
    sp = s.pop("source_pages", "")
    if sp:
        existing_notes = s.get("notes", "")
        s["notes"] = f"source_pages: {sp}" + (f"; {existing_notes}" if existing_notes else "")

with open(ds_path, "w", encoding="utf-8") as f:
    yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

print(f"Fixed {len(data['samples'])} samples")
for s in data["samples"]:
    print(f"  {s['id']}: notes={s.get('notes', '')}")
