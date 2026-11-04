import sqlite3
conn = sqlite3.connect(r"E:\Desktop\agent\backend\data\hardware_rag.db")
cur = conn.cursor()
cur.execute("SELECT kb_id, doc_id, title, status, chunk_count FROM knowledge_docs WHERE title LIKE '%bme280%' OR title LIKE '%nrf24l01%' OR title LIKE '%mpu6050%'")
for r in cur.fetchall():
    print(r)
conn.close()
