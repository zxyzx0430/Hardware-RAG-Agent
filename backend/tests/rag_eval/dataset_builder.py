"""
Dataset Builder — Generate golden dataset skeleton from existing config.py questions.

Reads the 15 questions in tests/rag_eval/config.py and generates a YAML skeleton
with question + reference_chunks fields pre-filled. The standard_answer field is
left empty (TODO) for manual completion by domain experts.

Usage:
    python -m tests.rag_eval.dataset_builder
    # Output: backend/tests/rag_eval/golden_dataset_skeleton.yaml
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from tests.rag_eval.config import QUESTIONS


# Map existing Q1-Q15 to G001-G015 IDs
def _q_id_to_g_id(q_id: str) -> str:
    """Convert Q1 -> G001, Q15 -> G015."""
    num = int(q_id[1:])
    return f"G{num:03d}"


def build_skeleton() -> dict:
    """Build golden dataset skeleton from config.py questions."""
    samples = []
    for q in QUESTIONS:
        g_id = _q_id_to_g_id(q.id)

        # Build reference_chunks from expected_keywords + chunk_cooccur_groups
        # This is a rough skeleton — human expert should refine
        ref_chunk_parts = []
        for keyword_group in q.expected_keywords:
            ref_chunk_parts.append("/".join(keyword_group))
        ref_chunk_text = "；".join(ref_chunk_parts)

        cooccur_text = ""
        if q.chunk_cooccur_groups:
            cooccur_text = " 共现要求：" + " | ".join([
                "+".join(group) for group in q.chunk_cooccur_groups
            ])

        sample = {
            "id": g_id,
            "question": q.question,
            "standard_answer": "",  # TODO: fill in by domain expert
            "reference_chunks": [
                f"[TODO] 参考chunk文本：{ref_chunk_text}{cooccur_text}"
            ],
            "difficulty": q.difficulty,
            "category": "TODO",
            "target_doc": q.target_doc,
            "tags": [],
            "notes": f"Converted from config.py {q.id}",
        }
        samples.append(sample)

    return {
        "metadata": {
            "version": "0.1-skeleton",
            "description": "Auto-generated skeleton from config.py — standard_answer needs manual completion",
            "created": "2026-06-27",
            "sample_count": len(samples),
            "fields": {
                "id": "G001-G999",
                "question": "from config.py",
                "standard_answer": "TODO — 人工填写",
                "reference_chunks": "TODO — 从 expected_keywords 推断，需人工核实",
                "difficulty": "from config.py",
                "category": "TODO — 人工填写",
                "target_doc": "from config.py",
                "tags": "TODO — 人工填写",
                "notes": "转换来源标注",
            },
        },
        "samples": samples,
    }


def main():
    skeleton = build_skeleton()
    output_path = Path(__file__).resolve().parent / "golden_dataset_skeleton.yaml"
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(skeleton, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    print(f"Skeleton generated: {output_path}")
    print(f"  {len(skeleton['samples'])} samples")
    print(f"  Next step: fill in standard_answer + reference_chunks manually")


if __name__ == "__main__":
    main()
