"""Page-image route: rendering, validation, errors and caching with a temp PDF."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import kb_routes
from app.db.models import KnowledgeBase, KnowledgeDoc
from src.rag import page_image

pytest.importorskip("fitz")


def _write_pdf(path: Path, pages: int = 2) -> None:
    import fitz

    if path.exists():
        path.unlink()
    document = fitz.open()
    try:
        for index in range(pages):
            page = document.new_page(width=200, height=300)
            page.insert_text((20, 40), f"page {index + 1}")
        document.save(str(path))
    finally:
        document.close()


@pytest.fixture
def page_image_client(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    KnowledgeBase.__table__.create(engine)
    KnowledgeDoc.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        db.add(KnowledgeBase(id="kb-test", name="Test KB", collection_name="page-image-test"))
        db.add_all([
            KnowledgeDoc(doc_id="pdf-doc", kb_id="kb-test", title="manual.pdf", file_type="pdf"),
            KnowledgeDoc(doc_id="md-doc", kb_id="kb-test", title="notes.md", file_type="md"),
            KnowledgeDoc(doc_id="missing-doc", kb_id="kb-test", title="gone.pdf", file_type="pdf"),
            KnowledgeDoc(doc_id="orphan-doc", kb_id="kb-gone", title="orphan.pdf", file_type="pdf"),
            KnowledgeDoc(doc_id="corrupt-doc", kb_id="kb-test", title="broken.pdf", file_type="pdf"),
            KnowledgeDoc(doc_id="huge-doc", kb_id="kb-test", title="huge.pdf", file_type="pdf"),
            KnowledgeDoc(doc_id="weird-doc", kb_id="kb-test", title='..\\evil"name.pdf', file_type="pdf"),
            KnowledgeDoc(doc_id="unicode-doc", kb_id="kb-test", title="硬件手册.pdf", file_type="pdf"),
        ])
        db.commit()

    @contextmanager
    def db_context():
        with session_factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    _write_pdf(tmp_path / "pdf-doc.pdf", pages=2)
    _write_pdf(tmp_path / "weird-doc.pdf", pages=1)
    _write_pdf(tmp_path / "unicode-doc.pdf", pages=1)
    (tmp_path / "md-doc.md").write_text("# notes", encoding="utf-8")
    (tmp_path / "corrupt-doc.pdf").write_bytes(b"%PDF-1.4\nbroken\n")

    import fitz

    huge = fitz.open()
    try:
        huge.new_page(width=5000, height=5000)
        huge.save(str(tmp_path / "huge-doc.pdf"))
    finally:
        huge.close()

    monkeypatch.setattr(kb_routes, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(kb_routes, "get_db_ctx", db_context)
    page_image.clear_cache()

    app = FastAPI()
    app.include_router(kb_routes.router)
    with TestClient(app) as client:
        yield client
    engine.dispose()


def _error(response) -> dict:
    payload = response.json()
    assert payload["success"] is False
    return payload["error"]


def test_renders_png_with_cache_headers(page_image_client):
    response = page_image_client.get(
        "/api/kb/documents/pdf-doc/page-image", params={"page": 1},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert "max-age" in response.headers["cache-control"]
    assert response.headers["x-page-image-cache"] == "miss"


def test_cache_hit_avoids_second_render(page_image_client, monkeypatch):
    calls = []
    original = page_image._render_page_bytes

    def recording(path, page, scale):
        calls.append((path, page, scale))
        return original(path, page, scale)

    monkeypatch.setattr(page_image, "_render_page_bytes", recording)
    url = "/api/kb/documents/pdf-doc/page-image"
    params = {"page": 2, "scale": 1.0}

    first = page_image_client.get(url, params=params)
    second = page_image_client.get(url, params=params)

    assert first.status_code == second.status_code == 200
    assert second.content == first.content
    assert len(calls) == 1
    assert first.headers["x-page-image-cache"] == "miss"
    assert second.headers["x-page-image-cache"] == "hit"


def test_cache_separates_page_and_scale(page_image_client, monkeypatch):
    calls = []
    original = page_image._render_page_bytes

    def recording(path, page, scale):
        calls.append((page, scale))
        return original(path, page, scale)

    monkeypatch.setattr(page_image, "_render_page_bytes", recording)
    url = "/api/kb/documents/pdf-doc/page-image"

    for params in (
        {"page": 1, "scale": 1.0},
        {"page": 2, "scale": 1.0},
        {"page": 1, "scale": 0.5},
        {"page": 1, "scale": 0.5},
    ):
        assert page_image_client.get(url, params=params).status_code == 200

    assert calls == [(1, 1.0), (2, 1.0), (1, 0.5)]


def _png_size(content: bytes) -> tuple[int, int]:
    return int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big")


def test_scale_four_is_rendered(page_image_client):
    response = page_image_client.get(
        "/api/kb/documents/pdf-doc/page-image",
        params={"page": 1, "scale": 4.0},
    )

    assert response.status_code == 200
    assert _png_size(response.content) == (800, 1200)


def test_huge_page_stays_within_render_caps(page_image_client):
    response = page_image_client.get(
        "/api/kb/documents/huge-doc/page-image",
        params={"page": 1, "scale": 4.0},
    )

    assert response.status_code == 200
    width, height = _png_size(response.content)
    assert max(width, height) <= page_image.MAX_RENDER_SIDE
    assert width * height <= page_image.MAX_RENDER_PIXELS


def test_replaced_file_invalidates_cache(page_image_client, monkeypatch):
    calls = []
    original = page_image._render_page_bytes

    def recording(path, page, scale):
        calls.append((page, scale))
        return original(path, page, scale)

    monkeypatch.setattr(page_image, "_render_page_bytes", recording)
    url = "/api/kb/documents/pdf-doc/page-image"
    params = {"page": 1, "scale": 1.0}
    assert page_image_client.get(url, params=params).status_code == 200

    path = Path(kb_routes.UPLOAD_DIR) / "pdf-doc.pdf"
    _write_pdf(path, pages=3)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    second = page_image_client.get(url, params=params)

    assert second.status_code == 200
    assert second.headers["x-page-image-cache"] == "miss"
    assert len(calls) == 2


def test_page_out_of_range(page_image_client):
    response = page_image_client.get(
        "/api/kb/documents/pdf-doc/page-image", params={"page": 3},
    )

    assert response.status_code == 400
    error = _error(response)
    assert error["code"] == "PAGE_OUT_OF_RANGE"
    assert error["details"]["page_count"] == 2


def test_page_below_one_is_rejected(page_image_client):
    response = page_image_client.get(
        "/api/kb/documents/pdf-doc/page-image", params={"page": 0},
    )

    assert response.status_code == 400
    assert _error(response)["code"] == "INVALID_PAGE"


def test_scale_outside_allowed_range_is_rejected(page_image_client):
    response = page_image_client.get(
        "/api/kb/documents/pdf-doc/page-image", params={"page": 1, "scale": 4.5},
    )

    assert response.status_code == 400
    assert _error(response)["code"] == "INVALID_SCALE"


def test_missing_pdf_file(page_image_client):
    response = page_image_client.get(
        "/api/kb/documents/missing-doc/page-image", params={"page": 1},
    )

    assert response.status_code == 404
    assert _error(response)["code"] == "DOCUMENT_FILE_NOT_FOUND"


def test_non_pdf_document(page_image_client):
    response = page_image_client.get(
        "/api/kb/documents/md-doc/page-image", params={"page": 1},
    )

    assert response.status_code == 415
    error = _error(response)
    assert error["code"] == "DOCUMENT_NOT_PDF"
    assert error["details"] == {"file_type": "md"}


def test_unknown_document(page_image_client):
    response = page_image_client.get(
        "/api/kb/documents/unknown-doc/page-image", params={"page": 1},
    )

    assert response.status_code == 404
    assert _error(response)["code"] == "DOC_NOT_FOUND"


def test_document_without_its_kb(page_image_client):
    response = page_image_client.get(
        "/api/kb/documents/orphan-doc/page-image", params={"page": 1},
    )

    assert response.status_code == 404
    assert _error(response)["code"] == "KB_NOT_FOUND"


def test_corrupt_pdf_reports_render_failure(page_image_client):
    response = page_image_client.get(
        "/api/kb/documents/corrupt-doc/page-image", params={"page": 1},
    )

    assert response.status_code == 500
    assert _error(response)["code"] == "PAGE_RENDER_FAILED"


def test_path_like_doc_id_is_not_resolved(page_image_client):
    response = kb_routes.get_document_page_image(
        doc_id="../../secret", page=1, scale=1.0, user={"anonymous": True},
    )

    assert response.status_code == 404


def test_invalid_bearer_token_is_rejected(page_image_client):
    response = page_image_client.get(
        "/api/kb/documents/pdf-doc/page-image",
        params={"page": 1},
        headers={"Authorization": "Bearer not-a-real-session"},
    )

    assert response.status_code == 401
    payload = response.json()
    assert payload["detail"]["error"]["code"] == "AUTH_INVALID"


def test_missing_page_parameter_is_rejected(page_image_client):
    response = page_image_client.get("/api/kb/documents/pdf-doc/page-image")

    assert response.status_code == 422


def test_file_returns_original_pdf_inline(page_image_client):
    response = page_image_client.get("/api/kb/documents/pdf-doc/file")
    expected = (Path(kb_routes.UPLOAD_DIR) / "pdf-doc.pdf").read_bytes()

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("inline;")
    assert 'filename="manual.pdf"' in disposition
    assert "max-age" in response.headers["cache-control"]
    assert response.content == expected


def test_file_sanitizes_title_for_content_disposition(page_image_client):
    response = page_image_client.get("/api/kb/documents/weird-doc/file")

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    assert 'filename="evil_name.pdf"' in disposition


def test_file_supports_unicode_filename(page_image_client):
    response = page_image_client.get("/api/kb/documents/unicode-doc/file")

    assert response.status_code == 200
    assert "filename*=utf-8''" in response.headers["content-disposition"]


def test_file_rejects_non_pdf(page_image_client):
    response = page_image_client.get("/api/kb/documents/md-doc/file")

    assert response.status_code == 415
    assert _error(response)["code"] == "DOCUMENT_NOT_PDF"


def test_file_missing_file(page_image_client):
    response = page_image_client.get("/api/kb/documents/missing-doc/file")

    assert response.status_code == 404
    assert _error(response)["code"] == "DOCUMENT_FILE_NOT_FOUND"


def test_file_unknown_document(page_image_client):
    response = page_image_client.get("/api/kb/documents/unknown-doc/file")

    assert response.status_code == 404
    assert _error(response)["code"] == "DOC_NOT_FOUND"


def test_file_path_like_doc_id_is_not_resolved(page_image_client):
    response = kb_routes.get_document_file(
        doc_id="../../secret", user={"anonymous": True},
    )

    assert response.status_code == 404


def test_file_invalid_bearer_token_is_rejected(page_image_client):
    response = page_image_client.get(
        "/api/kb/documents/pdf-doc/file",
        headers={"Authorization": "Bearer not-a-real-session"},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["error"]["code"] == "AUTH_INVALID"
