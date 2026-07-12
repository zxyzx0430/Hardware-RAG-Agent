"""Orchestrator for chunk-baseline-v2 Phase 1.

Runs cleanup + three MultimodalChunker reindexes sequentially,
each with a 30-minute timeout, then writes a summary JSON and
verifies collection/SQLite state.

Run:
    python scripts/run_chunk_baseline_v2.py
    python scripts/run_chunk_baseline_v2.py --model oc/mimo-v2.5
"""
import argparse
import json
import logging
import sqlite3
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"

sys.path.insert(0, str(BACKEND_DIR))
from src.config.settings import settings  # noqa: E402

CHROMA_DIR = Path(settings.chroma_persist_dir)
SQLITE_PATH = Path(settings.sqlite_db_path)
COLLECTION_NAME = "hardware-docs-test"
KB_ID = "builtin-001"
BENCHMARK_DIR = PROJECT_ROOT / "data" / "benchmark"
SUMMARY_PATH = BENCHMARK_DIR / "reindex_v2_summary.json"
REINDEX_SCRIPTS = [
    ("ch340g", "scripts/reindex_baseline_ch340g.py", "ch340g_chunks_v2.json"),
    ("stm32f4", "scripts/reindex_baseline_stm32f4.py", "stm32f4_chunks_v2.json"),
    ("esp32", "scripts/reindex_baseline_esp32.py", "esp32_chunks_v2.json"),
]

# Expected deterministic doc IDs (ch340g is UUID, discovered from stdout).
EXPECTED_DOC_IDS = {
    "stm32f4": "baseline-stm32f4-gpio-exti-v2",
    "esp32": "baseline-esp32-datasheet-v2",
}


def parse_summary(stdout: str) -> dict:
    """Parse the '=== Reindex Summary ===' block from script stdout."""
    summary = {}
    in_block = False
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped == "=== Reindex Summary ===":
            in_block = True
            continue
        if stripped == "=======================":
            break
        if in_block and ":" in stripped:
            key, _, value = stripped.partition(":")
            summary[key.strip().lower().replace(" ", "_")] = value.strip()
    return summary


def run_cleanup() -> None:
    """Run cleanup_baseline_kb.py."""
    cmd = [sys.executable, "scripts/cleanup_baseline_kb.py"]
    logger.info(f"Running cleanup: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    logger.info(result.stdout)
    if result.returncode != 0:
        logger.error(result.stderr)
        raise RuntimeError(f"Cleanup failed with exit code {result.returncode}")


def run_reindex(name: str, script: str, model_override: str, timeout: int = 1800) -> dict:
    """Run a single reindex script and return parsed summary."""
    cmd = [sys.executable, script]
    if model_override:
        cmd.extend(["--model", model_override])
    logger.info(f"Running reindex [{name}]: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=timeout)
    logger.info(result.stdout)
    if result.stderr:
        logger.warning(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"Reindex [{name}] failed with exit code {result.returncode}")

    summary = parse_summary(result.stdout)
    if not summary:
        raise RuntimeError(f"Reindex [{name}] did not produce a summary block")
    return summary


def read_snapshot_counts(snapshot_name: str) -> tuple[int, int]:
    """Return (total_chunks, image_description_chunks) from a snapshot file."""
    path = BENCHMARK_DIR / snapshot_name
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        total = len(data)
        img = sum(1 for c in data if c.get("metadata", {}).get("content_type") == "image_description")
        return total, img
    # Fallback: nested dict format
    total = data.get("total_chunks", 0)
    img = data.get("image_description_chunks", data.get("image_chunks", 0))
    return total, img


def get_chroma_count() -> int:
    """Return total chunks in the target ChromaDB collection."""
    import chromadb

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    try:
        collection = client.get_collection(COLLECTION_NAME)
        return len(collection.get().get("ids", []))
    except Exception as exc:
        raise RuntimeError(f"Failed to access ChromaDB collection {COLLECTION_NAME}: {exc}") from exc


def get_doc_rows(doc_ids: set[str]) -> dict[str, int]:
    """Return chunk_count per doc_id from SQLite knowledge_docs."""
    if not SQLITE_PATH.exists():
        raise RuntimeError(f"SQLite DB not found: {SQLITE_PATH}")
    conn = sqlite3.connect(str(SQLITE_PATH))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT doc_id, chunk_count FROM knowledge_docs WHERE kb_id = ?",
            (KB_ID,),
        )
        rows = {doc_id: chunk_count for doc_id, chunk_count in cur.fetchall()}
        return {doc_id: rows.get(doc_id) for doc_id in doc_ids}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run chunk-baseline-v2 Phase 1")
    parser.add_argument("--model", default="", help="Override vision model for all reindexes")
    args = parser.parse_args()

    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Cleanup
    run_cleanup()

    # 2. Reindex all three PDFs sequentially
    summaries: dict[str, dict] = {}
    for name, script, snapshot in REINDEX_SCRIPTS:
        summary = run_reindex(name, script, args.model)
        summaries[name] = summary

        # Validate deterministic doc IDs for stm32f4/esp32
        expected_id = EXPECTED_DOC_IDS.get(name)
        if expected_id and summary.get("doc_id") != expected_id:
            raise RuntimeError(
                f"[{name}] doc_id mismatch: expected {expected_id}, got {summary.get('doc_id')}"
            )

        # Snapshot sanity check
        total, img = read_snapshot_counts(snapshot)
        reported_total = int(summary.get("total_chunks", 0))
        if total != reported_total:
            logger.warning(
                f"[{name}] snapshot total ({total}) != reported total ({reported_total})"
            )

    # 3. Build summary JSON
    summary_records = []
    for name, script, snapshot in REINDEX_SCRIPTS:
        s = summaries[name]
        record = {
            "pdf": Path(script).stem.replace("reindex_baseline_", "") + ".pdf",
            "doc_id": s.get("doc_id"),
            "total_chunks": int(s.get("total_chunks", 0)),
            "text_chunks": int(s.get("text_chunks", 0)),
            "image_description_chunks": int(s.get("image_description_chunks", 0)),
            "elapsed_seconds": float(s.get("elapsed_seconds", 0)),
            "chunk_method": s.get("chunk_method", "multimodal"),
            "model": s.get("model", ""),
            "snapshot": snapshot,
        }
        summary_records.append(record)

    final_summary = {
        "kb_id": KB_ID,
        "collection_name": COLLECTION_NAME,
        "pdfs": summary_records,
        "total_chunks_all": sum(r["total_chunks"] for r in summary_records),
    }
    SUMMARY_PATH.write_text(json.dumps(final_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Summary saved: {SUMMARY_PATH}")

    # 4. Verify ChromaDB count
    chroma_count = get_chroma_count()
    expected_total = final_summary["total_chunks_all"]
    logger.info(f"ChromaDB collection count: {chroma_count}, expected: {expected_total}")
    if chroma_count != expected_total:
        raise RuntimeError(
            f"Verification failed: ChromaDB count {chroma_count} != expected {expected_total}"
        )

    # 5. Verify all doc IDs exist in SQLite
    doc_ids = {r["doc_id"] for r in summary_records}
    rows = get_doc_rows(doc_ids)
    missing = [doc_id for doc_id, count in rows.items() if count is None]
    if missing:
        raise RuntimeError(f"Verification failed: missing knowledge_docs rows: {missing}")
    logger.info(f"SQLite knowledge_docs verified for {len(rows)} doc(s)")

    # 6. Print final summary
    print("\n=== chunk-baseline-v2 Phase 1 Complete ===")
    print(f"Summary: {SUMMARY_PATH}")
    for r in summary_records:
        print(
            f"- {r['doc_id']}: {r['total_chunks']} chunks "
            f"({r['text_chunks']} text + {r['image_description_chunks']} image) "
            f"in {r['elapsed_seconds']:.1f}s using {r['model']}"
        )
    print(f"Total ingested: {chroma_count} chunks")
    print("==========================================\n")


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except subprocess.TimeoutExpired as exc:
        logger.error(f"Reindex timed out after 30 minutes: {exc}")
        sys.exit(1)
    except Exception as exc:
        logger.exception("chunk-baseline-v2 Phase 1 failed")
        sys.exit(1)
