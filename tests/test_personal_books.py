import asyncio
from pathlib import Path
from types import SimpleNamespace

from routes import personal_routes
from src.rag_vector import _generate_doc_id


class _PersonalDocs:
    def __init__(self):
        self.added = []
        self.excluded = []

    def add_directory(self, directory, index=False):
        self.added.append((directory, index))

    def exclude_file(self, filepath):
        self.excluded.append(filepath)


class _RAG:
    def __init__(self):
        self.batches = []
        self.deleted = []

    def _split_into_chunks(self, text, chunk_size=1000, overlap=200):
        return [text.strip()] if text.strip() else []

    def add_documents_batch(self, docs):
        self.batches.append(list(docs))
        return {"success": True, "added_count": len(docs), "failed_count": 0}

    def delete_by_source(self, source):
        self.deleted.append(source)
        return sum(len(batch) for batch in self.batches)


class _Upload:
    filename = "field-manual.pdf"

    async def read(self, _limit):
        return b"%PDF-1.7\nfixture"


def _request(collection="books"):
    class _AuthManager:
        def get_privileges(self, user):
            assert user == "alice"
            return {"can_use_documents": True}

    return SimpleNamespace(
        query_params={"collection": collection},
        state=SimpleNamespace(current_user="alice"),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=_AuthManager())),
        client=SimpleNamespace(host="203.0.113.10"),
    )


def _endpoint(router, path, method):
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and method in getattr(route, "methods", set())
    )


def test_books_upload_uses_owner_catalog_and_source_aware_batch(tmp_path, monkeypatch):
    docs = _PersonalDocs()
    rag = _RAG()
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(tmp_path))
    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: rag)
    monkeypatch.setattr(
        personal_routes,
        "extract_pdf_pages",
        lambda _path: ["page one full text", "page two full text"],
    )
    monkeypatch.setattr(personal_routes, "ocr_pdf_pages", lambda _path: ([], "unavailable"))
    router = personal_routes.setup_personal_routes(docs, None, True)

    result = asyncio.run(
        _endpoint(router, "/api/personal/upload", "POST")(
            request=_request(),
            files=[_Upload()],
        )
    )
    books = _endpoint(router, "/api/personal/books", "GET")(
        owner="alice",
    )["books"]

    assert result["success"] is True
    assert result["indexed_count"] == 2
    assert len(books) == 1
    expected = {
        "title": "field-manual",
        "filename": "field-manual.pdf",
        "page_count": 2,
        "status": "ready",
        "chunk_count": 2,
        "ocr_status": "not_needed",
        "needs_attention": False,
    }
    assert {key: books[0][key] for key in expected} == expected
    assert "source" not in books[0]
    books_dir = tmp_path / "alice" / "books"
    assert len(list(books_dir.glob("*.pdf"))) == 1
    assert books_dir.joinpath("catalog.json").is_file()
    assert len(rag.batches) == 1
    for page_number, (_text, metadata) in enumerate(rag.batches[0], start=1):
        assert metadata["owner"] == "alice"
        assert metadata["library"] == "books"
        assert metadata["page"] == page_number
        assert metadata["source_id"].startswith(f"book:{books[0]['id']}:page:{page_number}:")
    assert personal_routes._load_book_catalog("bob") == []


def test_image_only_book_is_kept_as_needs_ocr_when_native_ocr_is_unavailable(tmp_path, monkeypatch):
    docs = _PersonalDocs()
    rag = _RAG()
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(tmp_path))
    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: rag)
    monkeypatch.setattr(personal_routes, "extract_pdf_pages", lambda _path: ["", ""])
    monkeypatch.setattr(personal_routes, "ocr_pdf_pages", lambda _path: ([], "unavailable"))
    router = personal_routes.setup_personal_routes(docs, None, True)

    result = asyncio.run(
        _endpoint(router, "/api/personal/upload", "POST")(
            request=_request(),
            files=[_Upload()],
        )
    )
    book = _endpoint(router, "/api/personal/books", "GET")(owner="alice")["books"][0]

    assert result["needs_attention"] == 1
    assert book["status"] == "needs_attention"
    assert book["ocr_status"] == "unavailable"
    assert book["attention_reason"] == "needs_ocr"
    assert book["page_count"] == 2
    assert book["chunk_count"] == 0
    assert rag.batches == []


def test_books_reindex_and_delete_are_scoped_to_catalog_source(tmp_path, monkeypatch):
    docs = _PersonalDocs()
    rag = _RAG()
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(tmp_path))
    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: rag)
    monkeypatch.setattr(personal_routes, "extract_pdf_pages", lambda _path: ["indexed text"])
    monkeypatch.setattr(personal_routes, "ocr_pdf_pages", lambda _path: ([], "unavailable"))
    router = personal_routes.setup_personal_routes(docs, None, True)
    upload = _endpoint(router, "/api/personal/upload", "POST")
    asyncio.run(upload(request=_request(), files=[_Upload()]))
    book = _endpoint(router, "/api/personal/books", "GET")(owner="alice")["books"][0]

    reindexed = _endpoint(router, "/api/personal/books/{book_id}/reindex", "POST")(
        book_id=book["id"], owner="alice"
    )
    removed = _endpoint(router, "/api/personal/books/{book_id}", "DELETE")(
        book_id=book["id"], owner="alice"
    )

    assert reindexed["status"] == "ready"
    assert len(rag.deleted) == 2
    assert rag.deleted[0] == rag.deleted[1]
    assert removed["deleted_from_disk"] is True
    assert _endpoint(router, "/api/personal/books", "GET")(owner="alice")["books"] == []
    assert docs.excluded == [rag.deleted[0]]


def test_books_ui_and_responsive_root_rules_are_present():
    root = Path(__file__).resolve().parent.parent
    library_js = (root / "static/js/documentLibrary.js").read_text(encoding="utf-8")
    style = (root / "static/style.css").read_text(encoding="utf-8")

    assert 'data-doclib-tab="books"' in library_js
    assert 'data-doclib-panel="books"' in library_js
    assert "/api/personal/books" in library_js
    assert "/api/personal/upload?collection=books" in library_js
    assert 'role="tab"' in library_js
    assert "ArrowRight" in library_js and "ArrowLeft" in library_js
    assert ".first-run-step-state" in style and "white-space:normal" in style
    assert "#doclib-modal .doclib-modal-content" in style
    assert "min(1120px, calc(100vw - 32px))" in style


def test_source_aware_document_ids_do_not_collapse_identical_book_text():
    text = "shared text on two source pages"

    first = _generate_doc_id(text, "alice", "book:first:page:1:chunk:1")
    second = _generate_doc_id(text, "alice", "book:second:page:1:chunk:1")

    assert first != second
    assert _generate_doc_id(text, "alice") == _generate_doc_id(text, "alice")
