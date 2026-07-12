"""Check if page_start=1 chunks actually contain page-1 content."""
import json
from pathlib import Path

CHUNKS = Path(__file__).resolve().parent / "audit" / "ch340g_chunks_chroma.jsonl"
NEW_DOC_PREFIX = "32abb79e"


def main():
    chunks = []
    with open(CHUNKS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                c = json.loads(line)
                if c["metadata"].get("doc_id", "").startswith(NEW_DOC_PREFIX):
                    if c["metadata"].get("content_type") != "image_description":
                        chunks.append(c)

    page1_chunks = [c for c in chunks if c["metadata"].get("page_start") == 1]
    print(f"Chunks with page_start=1: {len(page1_chunks)}")

    for c in page1_chunks:
        section = c["metadata"].get("section_title", "")[:50]
        doc = c["document"][:300].replace("\n", " ")
        print(f"\n  section: {section}")
        print(f"  preview: {doc}")


if __name__ == "__main__":
    main()
