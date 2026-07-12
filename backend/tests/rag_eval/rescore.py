"""Re-score existing RAG eval results with updated _score_chunk_boundary.

Why: The old _score_chunk_boundary checked ALL chunks in the doc (200-300),
guaranteeing 0/10 for every strategy due to false positives accumulating.
The new version only checks the top-k source chunks returned by RAG retrieval.

This script:
1. Loads an existing JSON result file (to get questions, KB IDs, answers).
2. For each question, re-runs RAG retrieval (kb_manager.search) to get sources.
3. Re-runs _score_chunk_boundary and _score_cross_section with the new logic.
4. Keeps recall / answer_coverage / chunk_completeness from the old JSON
   (these don't depend on the bug).
5. Writes a new JSON + MD with updated scores.

Usage:
    python -m tests.rag_eval.rescore <input.json> [--output <output.json>]

If --output is omitted, writes <input>_rescored.json and .md.
"""
from __future__ import annotations

import argparse
import json
import sys
import re
from pathlib import Path
from datetime import datetime

# Force UTF-8 (Windows GBK console can't encode ✓/✗)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, Exception):
        pass

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.rag_eval.config import (
    QUESTIONS, SCORE_WEIGHTS, TOP_K, RELEVANCE_THRESHOLD,
    OUTPUT_DIR,
)


def rescore_chunk_boundary(sources: list[dict], weights: dict) -> tuple[float, str]:
    """Re-score chunk_boundary with enhanced code-block truncation detection.

    Checks BOTH per-chunk internal integrity AND cross-chunk boundary
    continuity. This is stricter than the old version which only checked
    if source chunks were individually "clean".

    Detection categories:
    1. Code fence imbalance (odd ``` count → split across boundary)
    2. Code block fragment at chunk start (content without opening ```)
    3. Code block truncated at chunk end (opening ``` without close)
    4. Inline code split (chunk ends/starts with single backtick)
    5. Table row split across boundary
    6. Markdown link split across boundary
    7. Sentence truncation (ASCII mid-word)
    8. Bare heading / list marker fragments
    9. Cross-chunk continuity: chunk N ends inside code block, chunk N+1
       starts with code content (not ```), confirming a truncation
    """
    chunk_texts = []
    for s in sources:
        excerpt = s.get("excerpt", "") or s.get("content", "")
        if excerpt:
            chunk_texts.append(excerpt)

    if not chunk_texts:
        return 0.0, "No source chunks returned"

    issues = []
    _NATURAL_END_CHARS = set("。！？.!?：:；;）)】》\"'\n```|-*#> \t")

    # Track code-block state across chunks for cross-boundary analysis
    _CODE_FENCE_RE = re.compile(r"```")

    for i, text in enumerate(chunk_texts):
        stripped = text.strip()
        if not stripped:
            continue

        lines = text.split("\n")
        code_fence_count = text.count("```")
        fence_positions = [m.start() for m in _CODE_FENCE_RE.finditer(text)]

        # ── 1. Code fence imbalance ──
        # Odd count means a code block is split across chunk boundary
        if code_fence_count % 2 != 0:
            # Determine if it's opening (truncated) or closing (fragment)
            if stripped.startswith("```"):
                issues.append(
                    f"src#{i}: code block TRUNCATED — opens ``` but no close "
                    f"(fences={code_fence_count})"
                )
            elif stripped.rstrip().endswith("```"):
                issues.append(
                    f"src#{i}: code block FRAGMENT — closes ``` but no open "
                    f"(fences={code_fence_count})"
                )
            else:
                issues.append(
                    f"src#{i}: code fence imbalance (count={code_fence_count}, "
                    f"likely split across boundary)"
                )

        # ── 2. Code content at chunk start without opening fence ──
        # If the first line looks like code (indented, ends with ; or { or }),
        # but there's no opening ```, it's a fragment of a split code block
        if code_fence_count % 2 != 0 and not stripped.startswith("```"):
            first_line = lines[0].strip() if lines else ""
            _CODE_INDICATORS = (";", "{", "}", "()", "->", "//", "/*", "*/",
                                "#include", "#define", "void ", "int ",
                                "uint", "static ", "return ", "if ", "for ")
            if any(ind in first_line for ind in _CODE_INDICATORS):
                issues.append(
                    f"src#{i}: code content at start without opening ``` "
                    f"(first_line='{first_line[:40]}...')"
                )

        # ── 3. Inline code split (single backtick at boundary) ──
        # Chunk ends with ` or starts with ` — inline code is split
        if stripped.endswith("`") and not stripped.endswith("```"):
            issues.append(f"src#{i}: ends with single backtick (inline code split)")
        if stripped.startswith("`") and not stripped.startswith("```"):
            # Only flag if it's not a legitimate inline code start
            # (heuristic: if the chunk starts with `word` pattern, it's fine)
            if not re.match(r"`[^\s`]+`", stripped):
                issues.append(f"src#{i}: starts with single backtick (inline code split)")

        # ── 4. Markdown link split ──
        # Chunk ends with [text or [text](url — incomplete link
        if re.search(r"\[[^\]]*$", stripped):
            issues.append(f"src#{i}: markdown link text split (unclosed '[')")
        if re.search(r"\]\([^\)]*$", stripped):
            issues.append(f"src#{i}: markdown link URL split (unclosed '](')")

        # ── 5. Table row split ──
        # Last line starts with | but doesn't end with | — row is cut
        if lines:
            last_line = lines[-1].rstrip()
            if last_line.strip().startswith("|") and not last_line.endswith("|"):
                issues.append(f"src#{i}: table row split (last line starts | but no closing |)")
            # First line ends with | but doesn't start with | — row fragment
            first_line = lines[0].strip()
            if first_line.endswith("|") and not first_line.startswith("|"):
                if len(lines) < 3 or not lines[1].strip().startswith("|"):
                    issues.append(f"src#{i}: table row fragment (first line ends | but no opening |)")

        # ── 6. Sentence truncation (ASCII only, CJK excluded) ──
        last_char = stripped[-1]
        if last_char not in _NATURAL_END_CHARS:
            if last_char.isascii() and last_char.isalpha():
                tail = stripped[-5:]
                ascii_alpha_tail = [c for c in tail if c.isascii() and c.isalpha()]
                if len(ascii_alpha_tail) >= 4:
                    issues.append(f"src#{i}: ends mid-word (tail='{tail}')")

        # ── 7. Bare heading fragment ──
        for j, line in enumerate(lines):
            line_s = line.strip()
            if line_s and len(line_s) <= 6 and all(c == "#" for c in line_s):
                issues.append(f"src#{i}: bare heading at line {j}")
                break

        # ── 8. Bare list marker at end ──
        if (stripped.endswith("-") or stripped.endswith("*")) and len(stripped) < 5:
            issues.append(f"src#{i}: ends with bare list marker")

        # ── 9. Very short code block (likely fragment) ──
        # If a chunk has exactly 2 fences but the content between them
        # is < 2 lines, it's probably a fragment of a larger code block
        if code_fence_count == 2 and len(fence_positions) == 2:
            code_content = text[fence_positions[0]+3:fence_positions[1]]
            code_lines = [l for l in code_content.strip().split("\n") if l.strip()]
            if len(code_lines) <= 1:
                issues.append(
                    f"src#{i}: code block too short ({len(code_lines)} line) — "
                    f"likely fragment"
                )

    # ── 10. Cross-chunk continuity check ──
    # If chunk N has odd fence count (ends inside code block) AND
    # chunk N+1 also has odd fence count (starts inside code block),
    # the code block is split across the boundary
    for i in range(len(chunk_texts) - 1):
        curr_fences = chunk_texts[i].count("```")
        next_fences = chunk_texts[i + 1].count("```")
        if curr_fences % 2 != 0 and next_fences % 2 != 0:
            issues.append(
                f"src#{i}→#{i+1}: code block SPLIT across boundary "
                f"(fences: {curr_fences}→{next_fences})"
            )

    if not issues:
        return float(weights["chunk_boundary"]), f"All {len(chunk_texts)} src chunks clean (code blocks intact)"

    n_issues = len(issues)
    if n_issues == 1:
        deduction = 3
    elif n_issues <= 3:
        deduction = 6
    else:
        deduction = 10
    score = max(0.0, weights["chunk_boundary"] - deduction)
    return score, f"{n_issues} issues in {len(chunk_texts)} srcs: {'; '.join(issues[:3])}"


def rescore_cross_section(q, sources: list[dict], weights: dict) -> tuple[float, str]:
    """Re-score cross_section with three-tier logic (same as run_eval.py)."""
    if not q.cross_section_keywords or len(q.cross_section_keywords) < 2:
        return float(weights["cross_section"]), "No cross-section keywords defined"

    kw_pair = q.cross_section_keywords[:2]
    kw1, kw2 = kw_pair[0], kw_pair[1]

    # Find chunk indices where each keyword appears
    kw1_chunks = []
    kw2_chunks = []
    for idx, s in enumerate(sources):
        text = (s.get("excerpt", "") or s.get("content", "")).lower()
        if kw1.lower() in text:
            kw1_chunks.append(idx)
        if kw2.lower() in text:
            kw2_chunks.append(idx)

    if not kw1_chunks or not kw2_chunks:
        return 0.0, f"Keywords not found: {kw1}={len(kw1_chunks)}, {kw2}={len(kw2_chunks)}"

    # Find minimum distance
    best_distance = float("inf")
    for i1 in kw1_chunks:
        for i2 in kw2_chunks:
            dist = abs(i1 - i2)
            if dist < best_distance:
                best_distance = dist

    if best_distance == 0:
        score = float(weights["cross_section"])
        detail = f"✓ {kw1}+{kw2} in same chunk#{kw1_chunks[0]}"
    elif best_distance == 1:
        score = weights["cross_section"] * 0.8
        detail = f"~ {kw1}+{kw2} in adjacent chunks (dist=1)"
    else:
        score = weights["cross_section"] * 0.3
        detail = f"△ {kw1}+{kw2} far apart (dist={best_distance})"
    return score, detail


def rescore_strategy(strategy_data: dict, kb_manager) -> dict:
    """Re-score a single strategy's results using KB retrieval."""
    kb_id = strategy_data.get("kb_id")
    if not kb_id:
        print(f"    ⚠ No kb_id in strategy {strategy_data.get('name')}, skipping")
        return strategy_data

    print(f"    Re-scoring with KB {kb_id}...")

    new_question_results = []
    new_dim_scores = {"recall": 0, "answer_coverage": 0, "chunk_completeness": 0,
                       "chunk_boundary": 0, "cross_section": 0}

    for qr in strategy_data.get("question_results", []):
        qid = qr.get("question_id", "")
        # Find matching question config
        q_config = next((q for q in QUESTIONS if q.id == qid), None)
        if not q_config:
            print(f"      ⚠ Question {qid} not found in config, keeping old scores")
            new_question_results.append(qr)
            continue

        # Re-run RAG retrieval (no LLM call)
        try:
            results = kb_manager.search(kb_id, q_config.question, k=TOP_K,
                                         score_threshold=RELEVANCE_THRESHOLD)
        except Exception as e:
            print(f"      ⚠ Search failed for {qid}: {e}, keeping old scores")
            new_question_results.append(qr)
            continue

        # Build sources list (same format as chat API returns)
        sources = []
        for r in results:
            sources.append({
                "title": r.metadata.get("title", ""),
                "excerpt": r.content,
                "content": r.content,
                "doc_id": r.doc_id,
                "chunk_id": r.metadata.get("chunk_id", ""),
                "score": r.score,
            })

        # Re-score chunk_boundary and cross_section
        new_boundary, boundary_detail = rescore_chunk_boundary(sources, SCORE_WEIGHTS)
        new_cross, cross_detail = rescore_cross_section(q_config, sources, SCORE_WEIGHTS)

        # Keep old scores for recall, answer_coverage, chunk_completeness
        old_scores = qr.get("scores", {})
        new_scores = {
            "recall": old_scores.get("recall", {}),
            "answer_coverage": old_scores.get("answer_coverage", {}),
            "chunk_completeness": old_scores.get("chunk_completeness", {}),
            "chunk_boundary": {"score": new_boundary, "max": SCORE_WEIGHTS["chunk_boundary"], "detail": boundary_detail},
            "cross_section": {"score": new_cross, "max": SCORE_WEIGHTS["cross_section"], "detail": cross_detail},
        }

        # Recompute total
        total = sum(s.get("score", 0) for s in new_scores.values())

        # Update dimension accumulators
        for dim in new_dim_scores:
            new_dim_scores[dim] += new_scores[dim].get("score", 0)

        new_qr = {
            **qr,
            "scores": new_scores,
            "total_score": total,
            "sources_count": len(sources),
            "source_titles": [s.get("title", "") for s in sources[:5]],
            "sources_full": [
                {
                    "title": s.get("title", ""),
                    "excerpt": (s.get("excerpt", "") or "")[:2000],
                    "doc_id": s.get("doc_id", ""),
                    "chunk_id": s.get("chunk_id", ""),
                }
                for s in sources
            ],
        }
        new_question_results.append(new_qr)

        dim_summary = (f"boundary={new_boundary:.1f}, cross={new_cross:.1f}")
        print(f"      Q{qid}: {len(sources)} srcs → {dim_summary}")

    # Recompute strategy totals
    n = max(1, len(new_question_results))
    new_dim_scores = {k: v for k, v in new_dim_scores.items()}
    new_total = sum(new_dim_scores.values())

    return {
        **strategy_data,
        "question_results": new_question_results,
        "dimension_scores": new_dim_scores,
        "total_score": new_total,
        "max_score": sum(SCORE_WEIGHTS.values()) * n,
        "rescored_at": datetime.now().isoformat(),
    }


def generate_md(data: dict, output_path: Path):
    """Generate markdown report from rescored data."""
    lines = [
        f"# RAG 评估报告（重评分）— {data.get('timestamp', '?')}",
        "",
        f"- Model: `{data.get('model', '?')}`",
        f"- Questions: {data.get('total_questions', '?')}",
        f"- Rescored at: {data.get('rescored_at', datetime.now().isoformat())}",
        "",
        "## 评分维度",
        "",
        f"- recall (30分): 召回命中率",
        f"- answer_coverage (25分): 回答关键词覆盖",
        f"- chunk_completeness (25分): chunk 语义完整性",
        f"- chunk_boundary (10分): chunk 边界质量 (**重评分**: 只检查 top-k sources)",
        f"- cross_section (10分): 跨章节关联 (**重评分**: 三层评分)",
        "",
        "## 策略对比",
        "",
        "| 策略 | 总分 | 百分比 | recall | answer | chunk_comp | boundary | cross_sec |",
        "|------|------|--------|--------|--------|------------|----------|-----------|",
    ]

    for s in sorted(data.get("strategies", []), key=lambda x: -x.get("total_score", 0)):
        name = s.get("name", "?")
        total = s.get("total_score", 0)
        max_score = s.get("max_score", 1000)
        pct = total / max_score * 100 if max_score else 0
        dims = s.get("dimension_scores", {})
        r = dims.get("recall", 0)
        a = dims.get("answer_coverage", 0)
        c = dims.get("chunk_completeness", 0)
        b = dims.get("chunk_boundary", 0)
        x = dims.get("cross_section", 0)
        lines.append(f"| {name} | {total:.1f}/{max_score} | {pct:.1f}% | {r:.1f} | {a:.1f} | {c:.1f} | {b:.1f} | {x:.1f} |")

    lines.extend(["", "## 详细结果", ""])

    for s in data.get("strategies", []):
        lines.append(f"### {s.get('name', '?')}")
        lines.append(f"- KB: `{s.get('kb_id', '?')}`")
        lines.append(f"- 总分: {s.get('total_score', 0):.1f}/{s.get('max_score', 1000)}")
        lines.append("")
        lines.append("| Q | 难度 | 总分 | recall | answer | chunk_comp | boundary | cross_sec |")
        lines.append("|---|------|------|--------|--------|------------|----------|-----------|")
        for qr in s.get("question_results", []):
            qid = qr.get("question_id", "?")
            diff = qr.get("difficulty", "?")
            total = qr.get("total_score", 0)
            sc = qr.get("scores", {})
            lines.append(
                f"| {qid} | {diff} | {total:.1f} | "
                f"{sc.get('recall', {}).get('score', 0):.1f} | "
                f"{sc.get('answer_coverage', {}).get('score', 0):.1f} | "
                f"{sc.get('chunk_completeness', {}).get('score', 0):.1f} | "
                f"{sc.get('chunk_boundary', {}).get('score', 0):.1f} | "
                f"{sc.get('cross_section', {}).get('score', 0):.1f} |"
            )
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  Markdown report: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Re-score RAG eval results with fixed chunk_boundary")
    parser.add_argument("input", help="Input JSON result file")
    parser.add_argument("--output", default="", help="Output JSON path (default: <input>_rescored.json)")
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"Error: {input_path} not found")
        sys.exit(1)

    output_path = Path(args.output).resolve() if args.output else \
        input_path.parent / f"{input_path.stem}_rescored.json"
    md_path = output_path.with_suffix(".md")

    print(f"Loading: {input_path}")
    data = json.loads(input_path.read_text(encoding="utf-8"))
    print(f"  Strategies: {len(data.get('strategies', []))}")

    # Initialize KB manager
    from src.rag.kb_manager import KnowledgeBaseManager
    kb_manager = KnowledgeBaseManager()

    # Rescore each strategy
    new_strategies = []
    for s in data.get("strategies", []):
        print(f"\n  Strategy: {s.get('name', '?')} (kb={s.get('kb_id', '?')})")
        new_s = rescore_strategy(s, kb_manager)
        new_strategies.append(new_s)

    data["strategies"] = new_strategies
    data["rescored_at"] = datetime.now().isoformat()
    data["rescore_note"] = (
        "chunk_boundary and cross_section re-scored with fixed logic: "
        "only checks top-k source chunks (not all doc chunks); "
        "CJK chars no longer flagged as mid-sentence; "
        "proportional deduction (1 issue=-2, 2-3=-5, 4+=-8) instead of 2 pts/issue."
    )

    # Print summary
    print(f"\n{'='*60}")
    print("Re-scored results:")
    print(f"{'='*60}")
    for s in sorted(new_strategies, key=lambda x: -x.get("total_score", 0)):
        name = s.get("name", "?")
        total = s.get("total_score", 0)
        max_score = s.get("max_score", 1000)
        pct = total / max_score * 100 if max_score else 0
        dims = s.get("dimension_scores", {})
        print(f"  {name:20} {total:7.1f}/{max_score} ({pct:.1f}%)  "
              f"boundary={dims.get('chunk_boundary', 0):.1f}  "
              f"cross={dims.get('cross_section', 0):.1f}")

    # Save
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  JSON saved: {output_path}")
    generate_md(data, md_path)


if __name__ == "__main__":
    main()
