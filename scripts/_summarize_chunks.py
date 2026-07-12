import json
from pathlib import Path

def summarize_ch340g():
    data = json.load(open("data/benchmark/ch340g_chunks.json", encoding="utf-8"))
    lines = []
    for c in data:
        m = c["metadata"]
        idx = m["chunk_index"]
        title = m.get("section_title", "")
        pages = f"p{m.get('section_page_start', '?')}-{m.get('section_page_end', '?')}"
        text = c["document"].replace("\n", " ")[:400]
        lines.append(f"[{idx}] {pages} | {title}\n{text}\n{'-'*80}")
    Path("data/benchmark/ch340g_chunks_summary.txt").write_text("\n\n".join(lines), encoding="utf-8")
    print("ch340g summary written")

def summarize_stm32f4():
    data = json.load(open("data/benchmark/stm32f4_chunks.json", encoding="utf-8"))
    lines = []
    for c in data["chunks"]:
        idx = c["chunk_index"]
        title = c.get("section_title", "")
        pages = f"p{c.get('page_start', '?')}-{c.get('page_end', '?')}"
        text = c.get("text_preview", "").replace("\n", " ")[:500]
        lines.append(f"[{idx}] {pages} | {title}\n{text}\n{'-'*80}")
    Path("data/benchmark/stm32f4_chunks_summary.txt").write_text("\n\n".join(lines), encoding="utf-8")
    print("stm32f4 summary written")

def summarize_esp32():
    data = json.load(open("data/benchmark/esp32_chunks.json", encoding="utf-8"))
    lines = []
    for c in data["chunks"]:
        idx = c["chunk_index"]
        title = c.get("section_title", "")
        pages = f"p{c.get('page_range', ['?','?'])[0]}-{c.get('page_range', ['?','?'])[1]}"
        text = c.get("text", c.get("text_preview", "")).replace("\n", " ")[:500]
        lines.append(f"[{idx}] {pages} | {title}\n{text}\n{'-'*80}")
    Path("data/benchmark/esp32_chunks_summary.txt").write_text("\n\n".join(lines), encoding="utf-8")
    print("esp32 summary written")

if __name__ == "__main__":
    summarize_ch340g()
    summarize_stm32f4()
    summarize_esp32()
