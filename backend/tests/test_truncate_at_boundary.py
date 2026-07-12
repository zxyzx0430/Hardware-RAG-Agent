"""Unit tests for truncate_at_boundary in src/rag/chunking/base.py."""
from src.rag.chunking.base import truncate_at_boundary


class TestTruncateAtBoundary:
    """Tests for boundary-aware truncation."""

    def test_short_text_no_truncation(self):
        """Short text within max_chars + tolerance should return unchanged."""
        assert truncate_at_boundary("short", 4000) == "short"

    def test_exact_boundary_length_no_truncation(self):
        """Text exactly at max_chars + tolerance should return unchanged."""
        text = "A" * 4100
        assert truncate_at_boundary(text, 4000, 100) == text

    def test_paragraph_boundary_truncation(self):
        """Should cut at paragraph boundary (\\n\\n) when available."""
        text = "A" * 3950 + "\n\n" + "B" * 200
        result = truncate_at_boundary(text, 4000, 100)
        assert result.endswith("\n\n")
        assert len(result) == 3952

    def test_chinese_period_boundary_truncation(self):
        """Should cut at Chinese period (。) when no paragraph boundary."""
        text = "A" * 3980 + "。" + "B" * 200
        result = truncate_at_boundary(text, 4000, 100)
        assert result.endswith("。")
        assert len(result) == 3981

    def test_english_period_boundary_truncation(self):
        """Should cut at English period (.) when no higher-priority boundary."""
        text = "A" * 3980 + "." + "B" * 200
        result = truncate_at_boundary(text, 4000, 100)
        assert result.endswith(".")
        assert len(result) == 3981

    def test_hard_cut_no_boundary(self):
        """Should hard-cut at max_chars when no boundary found in window."""
        text = "A" * 5000
        result = truncate_at_boundary(text, 4000, 100)
        assert len(result) == 4000
        assert result == "A" * 4000

    def test_hard_cut_boundary_outside_window(self):
        """Boundary outside tolerance window should trigger hard cut."""
        # Paragraph boundary at position 200, far outside [3900, 4100]
        text = "A" * 200 + "\n\n" + "B" * 5000
        result = truncate_at_boundary(text, 4000, 100)
        assert len(result) == 4000

    def test_newline_boundary_truncation(self):
        """Should cut at single newline (\\n) when no higher-priority boundary."""
        text = "A" * 3970 + "\n" + "B" * 200
        result = truncate_at_boundary(text, 4000, 100)
        assert result.endswith("\n")
        assert len(result) == 3971

    def test_paragraph_preferred_over_period(self):
        """Paragraph boundary should be preferred over period even if closer."""
        # Period at 4005, paragraph at 3990 — paragraph wins by priority
        text = "A" * 3989 + "\n\n" + "B" * 10 + "。" + "C" * 100
        result = truncate_at_boundary(text, 4000, 100)
        assert result.endswith("\n\n")
        assert len(result) == 3991

    def test_forward_boundary_preferred_when_closer(self):
        """Forward boundary closer to max_chars should be chosen over backward."""
        # Backward \n\n at 3950, forward \n\n at 4050 — forward is closer (50 vs 50, tie → backward)
        # Use 3940 and 4060 to make forward clearly closer
        text = "A" * 3940 + "\n\n" + "B" * 114 + "\n\n" + "C" * 100
        result = truncate_at_boundary(text, 4000, 100)
        # forward at 4056 (3940 + 2 + 114), backward at 3940
        # forward_dist = 56, backward_dist = 60 → forward wins
        assert "\n\n" in result
        assert len(result) > 4000  # forward cut exceeds max_chars
