"""Analyze retrieval gaps for chunk-baseline-golden-v1."""
import json
from pathlib import Path

import yaml

YAML_PATH = Path("data/benchmark/chunk-baseline-golden-v1.yaml")
REPORT_PATH = Path("data/benchmark/chunk-baseline-golden-v1-validation-report.json")
JSON_OUT = Path("data/benchmark/chunk-baseline-golden-v1-retrieval-gap-report.json")
MD_OUT = Path("data/benchmark/chunk-baseline-golden-v1-retrieval-gap-report.md")
DISTANCE_GAP_THRESHOLD = 1.0


def load_data(yaml_path: Path, report_path: Path):
    with yaml_path.open("r", encoding="utf-8") as f:
        golden = yaml.safe_load(f)
    with report_path.open("r", encoding="utf-8") as f:
        report = json.load(f)
    return golden["samples"], report


def pdf_key_from_sample(sample: dict) -> str:
    return sample["id"].split("-")[0]


def parse_page_range(page_range: str) -> tuple[int, int]:
    start, end = page_range.split("-")
    return int(start), int(end)


def has_page_overlap(source_pages: list[int], chunk_range: str) -> bool:
    start, end = parse_page_range(chunk_range)
    return any(start <= page <= end for page in source_pages)


def check_relevant_gap(sample: dict, retrieved: list[dict]) -> bool:
    top_ids = {r["chunk_id"] for r in retrieved}
    return any(cid not in top_ids for cid in sample["relevant_chunks"])


def check_source_page_mismatch(sample: dict, retrieved: list[dict]) -> bool:
    top3 = retrieved[:3]
    return not any(has_page_overlap(sample["source_pages"], r["page_range"]) for r in top3)


def check_distance_gap(retrieved: list[dict]) -> bool:
    return retrieved[0]["distance"] > DISTANCE_GAP_THRESHOLD


def analyze_question(sample: dict, retrieved: list[dict]) -> dict:
    distances = [r["distance"] for r in retrieved[:3]]
    return {
        "id": sample["id"],
        "query": sample["query"],
        "question_type": sample["question_type"],
        "source_pages": sample["source_pages"],
        "relevant_chunks": sample["relevant_chunks"],
        "top3_distances": distances,
        "avg_top3_distance": round(sum(distances) / len(distances), 4),
        "relevant_gap": check_relevant_gap(sample, retrieved),
        "source_page_mismatch": check_source_page_mismatch(sample, retrieved),
        "distance_gap": check_distance_gap(retrieved),
    }


def summarize_pdf(pdf_key: str, samples: list[dict], report: dict) -> dict:
    questions = [analyze_question(s, report[pdf_key][i]["retrieved"]) for i, s in enumerate(samples)]
    type_counts = {}
    for q in questions:
        type_counts.setdefault(q["question_type"], 0)
        type_counts[q["question_type"]] += 1
    return {
        "pdf_key": pdf_key,
        "total_questions": len(questions),
        "question_types": type_counts,
        "relevant_gaps": sum(1 for q in questions if q["relevant_gap"]),
        "source_page_mismatches": sum(1 for q in questions if q["source_page_mismatch"]),
        "distance_gaps": sum(1 for q in questions if q["distance_gap"]),
        "avg_top1_distance": round(
            sum(q["top3_distances"][0] for q in questions) / len(questions), 4
        ),
        "avg_top3_distance": round(
            sum(q["avg_top3_distance"] for q in questions) / len(questions), 4
        ),
        "questions": questions,
    }


def build_summary(pdfs: list[dict]) -> dict:
    total = sum(p["total_questions"] for p in pdfs)
    return {
        "total_questions": total,
        "pdfs": {p["pdf_key"]: p for p in pdfs},
        "overall_relevant_gaps": sum(p["relevant_gaps"] for p in pdfs),
        "overall_source_page_mismatches": sum(p["source_page_mismatches"] for p in pdfs),
        "overall_distance_gaps": sum(p["distance_gaps"] for p in pdfs),
        "has_retrieval_gap": any(
            p["relevant_gaps"] or p["distance_gaps"] for p in pdfs
        ),
    }


def build_markdown(summary: dict) -> str:
    lines = [
        "# chunk-baseline-golden-v1 Retrieval Gap Report",
        "",
        f"- Total questions: **{summary['total_questions']}**",
        f"- Overall relevant gaps: {summary['overall_relevant_gaps']}",
        f"- Overall source-page mismatches: {summary['overall_source_page_mismatches']}",
        f"- Overall distance gaps (> {DISTANCE_GAP_THRESHOLD}): {summary['overall_distance_gaps']}",
        f"- Has retrieval gap: **{'Yes' if summary['has_retrieval_gap'] else 'No'}**",
        "",
        "## Per-PDF Statistics",
        "",
        "| PDF | Questions | Types | Relevant Gaps | Source-Page Mismatches | Distance Gaps | Avg Top-1 Distance | Avg Top-3 Distance |",
        "|-----|-----------|-------|---------------|------------------------|---------------|--------------------|--------------------|",
    ]
    for pdf_key, pdf in summary["pdfs"].items():
        types = ", ".join(f"{k}={v}" for k, v in pdf["question_types"].items())
        lines.append(
            f"| {pdf_key} | {pdf['total_questions']} | {types} | "
            f"{pdf['relevant_gaps']} | {pdf['source_page_mismatches']} | {pdf['distance_gaps']} | "
            f"{pdf['avg_top1_distance']} | {pdf['avg_top3_distance']} |"
        )
    lines.extend(["", "## Question Details", ""])
    for pdf_key, pdf in summary["pdfs"].items():
        lines.append(f"### {pdf_key}")
        for q in pdf["questions"]:
            flags = []
            if q["relevant_gap"]:
                flags.append("relevant_gap")
            if q["source_page_mismatch"]:
                flags.append("source_page_mismatch")
            if q["distance_gap"]:
                flags.append("distance_gap")
            flag_str = ", ".join(flags) if flags else "OK"
            lines.append(
                f"- **{q['id']}** ({q['question_type']}) top3_dist={q['top3_distances']} → {flag_str}"
            )
        lines.append("")
    lines.extend([
        "",
        "## Notes",
        "",
        "- **Relevant gap**: a marked `relevant_chunk` is not in the top-5 retrieved results.",
        "- **Source-page mismatch**: none of the top-3 retrieved chunks overlap with the manually annotated `source_pages`. This can happen when chunk boundaries cross pages or when page annotations follow the original PDF rather than the chunk's `page_range`.",
        "- **Distance gap**: top-1 retrieval distance exceeds the threshold; indicates lower semantic confidence for that query.",
    ])
    return "\n".join(lines)


def group_samples_by_pdf(samples: list[dict]) -> dict:
    groups = {}
    for sample in samples:
        key = pdf_key_from_sample(sample)
        groups.setdefault(key, []).append(sample)
    return groups


def main():
    samples, report = load_data(YAML_PATH, REPORT_PATH)
    groups = group_samples_by_pdf(samples)
    pdfs = [summarize_pdf(key, group, report) for key, group in groups.items()]
    summary = build_summary(pdfs)
    with JSON_OUT.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    with MD_OUT.open("w", encoding="utf-8") as f:
        f.write(build_markdown(summary))
    print(f"Saved {JSON_OUT} and {MD_OUT}")
    print(f"Total questions: {summary['total_questions']}")
    print(f"Has retrieval gap: {summary['has_retrieval_gap']}")


if __name__ == "__main__":
    main()
