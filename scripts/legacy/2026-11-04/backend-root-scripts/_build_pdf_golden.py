"""Build golden dataset for the 44-page STM32F4 GPIO/EXTI extract PDF.

Based on my independent reading of the PDF (done via subagent), construct
5 questions with reference answers drawn from the actual PDF text.
"""
import httpx
import json
import re
from pathlib import Path

API = "http://127.0.0.1:58080/api"
DOC_ID = "5bb2b16f-7318-47f4-bf6e-a75af68c2223"
OUT = Path(r"E:/Desktop/agent/data/test_results")

r = httpx.get(f"{API}/kb/documents/{DOC_ID}/chunks", timeout=60)
chunks = r.json()["data"]["chunks"]
print(f"Total chunks: {len(chunks)}")

# Search for relevant chunks for each question
def find_chunks(keywords, chunks, max_results=5):
    scored = []
    for i, c in enumerate(chunks):
        content = c.get("content", "")
        score = sum(1 for kw in keywords if kw.lower() in content.lower())
        if score > 0:
            scored.append((score, i, content[:200]))
    scored.sort(reverse=True)
    return scored[:max_results]

questions = {
    "G001": {
        "query": "STM32F4 GPIO 有哪几种工作模式？MODER 寄存器中每种模式对应的二进制编码是什么？",
        "keywords": ["MODER", "input", "output", "analog", "00", "01", "10", "11", "Input floating", "GP output"],
        "analysis": "需要找到 MODER 寄存器描述中四种模式的位编码",
    },
    "G002": {
        "query": "STM32F4 GPIO 输出速度有几个等级？OSPEEDR 寄存器中各等级对应的二进制编码是什么？",
        "keywords": ["OSPEEDR", "speed", "low speed", "medium speed", "high speed", "very high"],
        "analysis": "需要找到 OSPEEDR 寄存器中四种速度等级的位编码",
    },
    "G003": {
        "query": "STM32F4 GPIO 锁定机制（LCKR）的具体操作步骤是什么？锁定序列是如何工作的？",
        "keywords": ["LCKR", "lock", "LOCK_KEY", "frozen", "sequence", "LKCK"],
        "analysis": "需要找到 LCKR 锁定序列的具体步骤",
    },
    "G004": {
        "query": "STM32F4 EXTI 中断线 5-9 和 10-15 为什么共享 NVIC 向量？如何配置 EXTI 中断？需要哪些寄存器？",
        "keywords": ["EXTI", "NVIC", "vector", "IRQn", "9_5", "15_10", "SYSCFG", "EXTICR", "interrupt"],
        "analysis": "需要找到 NVIC 向量表中 EXTI9_5 和 EXTI15_10 条目，以及 EXTI 配置流程",
    },
    "G005": {
        "query": "SYSCFG_EXTICR 寄存器如何选择 EXTI 中断的 GPIO 端口？各端口对应的编码是什么？",
        "keywords": ["SYSCFG", "EXTICR", "port", "select", "0000", "PA", "PB", "PC", "PD"],
        "analysis": "需要找到 EXTICR 寄存器中端口选择的位编码",
    },
}

results = []
for qid, qinfo in questions.items():
    print(f"\n{'='*60}")
    print(f"Question {qid}: {qinfo['query']}")
    print(f"{'='*60}")
    
    matches = find_chunks(qinfo["keywords"], chunks)
    for score, idx, preview in matches:
        m = chunks[idx].get("metadata", {})
        print(f"  [{idx}] score={score} pg={m.get('page_start')}-{m.get('page_end')} "
              f"sec=\"{m.get('section_title', '')[:40]}\" pre={preview[:80]!r}")
    
    # Build reference chunks list
    ref_chunks = []
    for score, idx, preview in matches:
        c = chunks[idx]
        m = c.get("metadata", {})
        ref_chunks.append({
            "chunk_index": idx,
            "score": score,
            "page_start": m.get("page_start"),
            "page_end": m.get("page_end"),
            "section_title": m.get("section_title", ""),
            "content_preview": preview,
        })
    
    results.append({
        "id": qid,
        "query": qinfo["query"],
        "analysis_note": qinfo["analysis"],
        "relevant_chunks": ref_chunks,
    })

# Save as golden dataset
golden = {
    "version": "pdf-golden-v1",
    "source": "stm32f4_gpio_exti_extract.pdf",
    "doc_id": DOC_ID,
    "kb_id": "kb-96eca485",
    "total_chunks": len(chunks),
    "questions": results,
}

with open(OUT / "pdf_golden_dataset.json", "w", encoding="utf-8") as f:
    json.dump(golden, f, ensure_ascii=False, indent=2)
print(f"\nSaved to {OUT / 'pdf_golden_dataset.json'}")
