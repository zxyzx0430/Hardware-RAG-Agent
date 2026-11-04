"""Test parsing and chunking 01-stm32-gpio.md fresh."""
import asyncio
from pathlib import Path

from src.rag.chunking import get_chunker
file_path = Path(r"E:\Desktop\agent\data\test_docs\01-stm32-gpio.md")

# Parse (markdown files are decoded directly in kb_routes)
text = file_path.read_bytes().decode("utf-8", errors="replace")
print(f"Parsed chars: {len(text)}")
print(f"Head:\n{text[:500]}")
print(f"Has EXTI: {'EXTI' in text}")
print(f"Has LCKR: {'LCKR' in text}")

# Chunk with hybrid defaults
async def main():
    chunker = get_chunker("hybrid")
    chunks = await chunker.chunk(text, {"title": file_path.name, "source": str(file_path)})
    print(f"\nChunk count: {len(chunks)}")
    total = sum(len(c.text) for c in chunks)
    print(f"Total chunk chars: {total}")
    print(f"Avg chunk len: {total / len(chunks):.1f}")

    # Find chunks mentioning EXTI / LCKR
    exti_chunks = [c for c in chunks if "EXTI" in c.text]
    lckr_chunks = [c for c in chunks if "LCKR" in c.text]
    print(f"EXTI chunks: {len(exti_chunks)}")
    print(f"LCKR chunks: {len(lckr_chunks)}")

    # Section titles
    sections = set(c.section_title for c in chunks)
    print(f"\nSections ({len(sections)}):")
    for s in sorted(sections):
        print(f"  {s}")

asyncio.run(main())
