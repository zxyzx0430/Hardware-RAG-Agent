import sqlite3
from pathlib import Path

DATA = Path(r"e:\Desktop\agent\backend\data")

def inspect(db_path: Path):
    if not db_path.exists() or db_path.stat().st_size == 0:
        print(f"\n{db_path.name}: EMPTY or missing")
        return
    try:
        c = sqlite3.connect(db_path)
        cur = c.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        print(f"\n{db_path.name} ({db_path.stat().st_size//1024}KB): tables={tables}")
        for t in tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM '{t}'")
                print(f"  {t}: {cur.fetchone()[0]} rows")
            except Exception as e:
                print(f"  {t}: err {e}")
        c.close()
    except Exception as e:
        print(f"\n{db_path.name}: open error {e}")

for f in ["chat_history.db", "hardware_agent.db", "knowledge.db",
          "test_checkpoint.sqlite", "chroma/chroma.sqlite3"]:
    inspect(DATA / f)
