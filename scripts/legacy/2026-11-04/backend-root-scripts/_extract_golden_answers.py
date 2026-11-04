"""Extract text for 5 golden questions from the 44-page STM32F4 GPIO/EXTI PDF."""
import fitz
import json
import re
from pathlib import Path

PDF = Path(r"E:/Desktop/agent/data/pdfs/mcu/stm32f4_gpio_exti_extract.pdf")
OUT = Path(r"E:/Desktop/agent/data/test_results")

doc = fitz.open(PDF)
all_text = ""
page_map = {}
for i, page in enumerate(doc):
    t = page.get_text("text")
    page_map[i + 1] = t
    all_text += f"\n<!-- PAGE:{i+1} -->\n{t}"

doc.close()

questions = {
    "G001": {
        "keywords": ["MODER", "input", "output", "alternate", "analog", "00", "01", "10", "11"],
        "pages": [1, 2, 3, 15, 16],
        "description": "GPIO四种模式及MODER寄存器值",
    },
    "G002": {
        "keywords": ["OSPEEDR", "speed", "MHz", "low", "medium", "high", "very high"],
        "pages": [1, 16, 17],
        "description": "GPIO输出速度等级",
    },
    "G003": {
        "keywords": ["LCKR", "lock", "sequence", "LKCK", "frozen"],
        "pages": [8, 9, 19],
        "description": "GPIO锁定机制LCKR",
    },
    "G004": {
        "keywords": ["EXTI", "interrupt", "vector", "NVIC", "IRQn", "9_5", "15_10", "SYSCFG", "EXTICR"],
        "pages": [22, 23, 24, 25, 26, 27, 28, 29, 30],
        "description": "EXTI中断配置与NVIC向量共享",
    },
    "G005": {
        "keywords": ["SYSCFG", "EXTICR", "port", "select", "PA", "PB", "PC", "PD"],
        "pages": [22, 23, 24, 25, 26],
        "description": "SYSCFG_EXTICR端口选择配置",
    },
}

results = {}
for qid, qinfo in questions.items():
    print(f"\n{'='*60}")
    print(f"Question {qid}: {qinfo['description']}")
    print(f"{'='*60}")
    combined = ""
    for p in qinfo["pages"]:
        if p in page_map:
            combined += f"\n--- Page {p} ---\n{page_map[p]}"
    results[qid] = {
        "description": qinfo["description"],
        "pages": qinfo["pages"],
        "text_length": len(combined),
        "text_preview": combined[:3000],
    }
    print(f"Pages: {qinfo['pages']}, chars: {len(combined)}")
    print(combined[:2000])

with open(OUT / "golden_answer_extracts.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\nSaved to {OUT / 'golden_answer_extracts.json'}")
