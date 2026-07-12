"""测试KB检索，为设计golden问题提供依据。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import chromadb
from sentence_transformers import SentenceTransformer

client = chromadb.PersistentClient(path="data/chroma")
coll = client.get_collection("hardware-docs-test")
model = SentenceTransformer("all-MiniLM-L6-v2")

queries = [
    "CH340G 5V供电电压范围是多少",
    "CH340G XI引脚晶振频率",
    "CH340G V3引脚在5V工作时接多大电容",
    "STM32F4 GPIO模拟模式 MODER 值",
    "STM32F4 EXTI 挂起寄存器怎么清除",
    "ESP32 Deep-sleep RTC timer 功耗",
    "ESP32 strapping pins 有哪些",
]

for q in queries:
    emb = model.encode(q).tolist()
    res = coll.query(query_embeddings=[emb], n_results=3, include=["documents", "metadatas", "distances"])
    print(f"\n=== {q} ===")
    for i in range(3):
        meta = res["metadatas"][0][i]
        doc = res["documents"][0][i]
        dist = res["distances"][0][i]
        print(f"  [{meta.get('chunk_index')}] {meta.get('title')} p{meta.get('page_range')} dist={dist:.4f}")
        print(f"      {doc[:200].replace(chr(10), ' ')}")
