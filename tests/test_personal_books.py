import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from fastapi import BackgroundTasks

from routes import personal_routes
from src import agent_loop
from src import personal_docs
from src.agent_identity import runtime_model_fact
from src.rag_vector import _generate_doc_id
from src.tools.system import do_manage_books


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


async def _upload_books(endpoint, request, files):
    background_tasks = BackgroundTasks()
    result = await endpoint(
        request=request,
        background_tasks=background_tasks,
        files=files,
    )
    await background_tasks()
    return result


def test_books_upload_uses_owner_catalog_and_source_aware_batch(tmp_path, monkeypatch):
    docs = _PersonalDocs()
    rag = _RAG()
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(tmp_path))
    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: rag)
    monkeypatch.setattr(
        personal_routes,
        "extract_pdf_pages",
        lambda _path: ["page one full text " * 10, "page two full text " * 10],
    )
    monkeypatch.setattr(personal_routes, "ocr_pdf_pages", lambda _path: ([], "unavailable"))
    router = personal_routes.setup_personal_routes(docs, None, True)

    result = asyncio.run(
        _upload_books(
            _endpoint(router, "/api/personal/upload", "POST"),
            _request(),
            [_Upload()],
        )
    )
    books = _endpoint(router, "/api/personal/books", "GET")(
        owner="alice",
    )["books"]

    assert result["success"] is True
    assert result["indexed_count"] == 0
    assert result["indexing_count"] == 1
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


def test_native_pdf_ocr_rasterizes_each_page_and_reads_tesseract_output(tmp_path, monkeypatch):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.7\nfixture")
    commands = []

    monkeypatch.setattr(personal_docs.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(personal_docs, "extract_pdf_pages", lambda _path: ["", ""])

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[0].endswith("tesseract"):
            return subprocess.CompletedProcess(command, 0, stdout=f"OCR page {len(commands) // 2}\n")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(personal_docs.subprocess, "run", fake_run)

    pages, status = personal_docs.ocr_pdf_pages(str(pdf))

    assert status == "complete"
    assert pages == ["OCR page 1\n", "OCR page 2\n"]
    assert [command[1:6] for command in commands[::2]] == [
        ["-f", "1", "-l", "1", "-r"],
        ["-f", "2", "-l", "2", "-r"],
    ]
    assert all(command[-3:] == ["stdout", "-l", "eng"] for command in commands[1::2])


def test_docker_image_bundles_scanned_pdf_ocr_dependencies():
    dockerfile = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text(encoding="utf-8")

    assert "tesseract-ocr" in dockerfile
    assert "tesseract-ocr-eng" in dockerfile
    assert "poppler-utils" in dockerfile


def test_image_only_book_is_kept_as_needs_ocr_when_native_ocr_is_unavailable(tmp_path, monkeypatch):
    docs = _PersonalDocs()
    rag = _RAG()
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(tmp_path))
    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: rag)
    sparse_pages = ["metadata fragments" * 16] + [""] * 156
    monkeypatch.setattr(personal_routes, "extract_pdf_pages", lambda _path: sparse_pages)
    monkeypatch.setattr(personal_routes, "ocr_pdf_pages", lambda _path: ([], "unavailable"))
    router = personal_routes.setup_personal_routes(docs, None, True)

    result = asyncio.run(
        _upload_books(
            _endpoint(router, "/api/personal/upload", "POST"),
            _request(),
            [_Upload()],
        )
    )
    book = _endpoint(router, "/api/personal/books", "GET")(owner="alice")["books"][0]

    assert result["needs_attention"] == 0
    assert result["indexing_count"] == 1
    assert book["status"] == "needs_attention"
    assert book["ocr_status"] == "unavailable"
    assert book["attention_reason"] == "needs_ocr"
    assert book["page_count"] == 157
    assert book["chunk_count"] == 0
    assert rag.batches == []

    background_tasks = BackgroundTasks()
    _endpoint(router, "/api/personal/books/{book_id}/reindex", "POST")(
        book_id=book["id"], background_tasks=background_tasks, owner="alice"
    )
    asyncio.run(background_tasks())
    assert rag.deleted == [personal_routes._load_book_catalog("alice")[0]["source"]]


def test_book_upload_returns_before_background_indexing(tmp_path, monkeypatch):
    docs = _PersonalDocs()
    rag = _RAG()
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(tmp_path))
    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: rag)
    monkeypatch.setattr(personal_routes, "extract_pdf_pages", lambda _path: ["full page text " * 20])
    router = personal_routes.setup_personal_routes(docs, None, True)
    endpoint = _endpoint(router, "/api/personal/upload", "POST")
    background_tasks = BackgroundTasks()

    result = asyncio.run(endpoint(
        request=_request(),
        background_tasks=background_tasks,
        files=[_Upload()],
    ))
    pending = _endpoint(router, "/api/personal/books", "GET")(owner="alice")["books"][0]

    assert result["indexing_count"] == 1
    assert pending["status"] == "indexing"
    assert rag.batches == []

    asyncio.run(background_tasks())
    ready = _endpoint(router, "/api/personal/books", "GET")(owner="alice")["books"][0]
    assert ready["status"] == "ready"
    assert len(rag.batches) == 1


def test_books_reindex_and_delete_are_scoped_to_catalog_source(tmp_path, monkeypatch):
    docs = _PersonalDocs()
    rag = _RAG()
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(tmp_path))
    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: rag)
    monkeypatch.setattr(personal_routes, "extract_pdf_pages", lambda _path: ["indexed full text " * 10])
    monkeypatch.setattr(personal_routes, "ocr_pdf_pages", lambda _path: ([], "unavailable"))
    router = personal_routes.setup_personal_routes(docs, None, True)
    upload = _endpoint(router, "/api/personal/upload", "POST")
    asyncio.run(_upload_books(upload, _request(), [_Upload()]))
    book = _endpoint(router, "/api/personal/books", "GET")(owner="alice")["books"][0]

    background_tasks = BackgroundTasks()
    reindexed = _endpoint(router, "/api/personal/books/{book_id}/reindex", "POST")(
        book_id=book["id"], background_tasks=background_tasks, owner="alice"
    )
    asyncio.run(background_tasks())
    assert reindexed["status"] == "indexing"
    current = _endpoint(router, "/api/personal/books", "GET")(owner="alice")["books"][0]
    assert current["status"] == "ready"

    removed = _endpoint(router, "/api/personal/books/{book_id}", "DELETE")(
        book_id=book["id"], owner="alice"
    )
    assert len(rag.deleted) == 2
    assert rag.deleted[0] == rag.deleted[1]
    assert removed["deleted_from_disk"] is True
    assert _endpoint(router, "/api/personal/books", "GET")(owner="alice")["books"] == []
    assert docs.excluded == [rag.deleted[0]]


def test_books_ui_and_responsive_root_rules_are_present():
    root = Path(__file__).resolve().parent.parent
    library_js = (root / "static/js/documentLibrary.js").read_text(encoding="utf-8")
    style = (root / "static/style.css").read_text(encoding="utf-8")
    chat_js = (root / "static/js/chat.js").read_text(encoding="utf-8")

    assert 'data-doclib-tab="books"' in library_js
    assert 'data-doclib-panel="books"' in library_js
    assert "/api/personal/books" in library_js
    assert "/api/personal/upload?collection=books" in library_js
    assert 'role="tab"' in library_js
    assert "ArrowRight" in library_js and "ArrowLeft" in library_js
    assert ".first-run-step-state" in style and "white-space:normal" in style
    assert "#doclib-modal .doclib-modal-content" in style
    assert "min(1120px, calc(100vw - 32px))" in style
    assert 'thinking-toggle live-think-toggle expanded' not in chat_js
    assert 'thinking-content expanded" id="${_liveThinkDomId}' not in chat_js
    assert "prepareExtensionTextTurn('oracle', streamSessionId)" in chat_js
    assert "json.extension_call" in chat_js


def test_source_aware_document_ids_do_not_collapse_identical_book_text():
    text = "shared text on two source pages"

    first = _generate_doc_id(text, "alice", "book:first:page:1:chunk:1")
    second = _generate_doc_id(text, "alice", "book:second:page:1:chunk:1")

    assert first != second
    assert _generate_doc_id(text, "alice") == _generate_doc_id(text, "alice")


def test_books_intent_routes_to_owner_scoped_catalog_not_filesystem():
    prompt = "Check my Books library. Which book needs OCR, and why?"
    intent = agent_loop._classify_agent_request([], prompt)

    assert "books" in intent["domains"]
    assert agent_loop._DOMAIN_TOOL_MAP["books"] == {"manage_books"}
    assert not (agent_loop._DOMAIN_TOOL_MAP["books"] & agent_loop._DOMAIN_TOOL_MAP["files"])
    assert "never use shell, grep" in agent_loop._DOMAIN_RULES["books"]
    assert not (agent_loop._DOMAIN_TOOL_MAP["books"] & agent_loop._DOMAIN_TOOL_MAP["workers"])


def test_runtime_identity_reports_selected_model_without_inventing_provider():
    fact = runtime_model_fact("jarvis")

    assert "model identifier: `jarvis`" in fact
    assert "unverified" in fact
    assert "OpenAI" not in fact
    assert "GPT-4" not in fact


def test_manage_books_search_is_owner_scoped_and_returns_page_provenance(monkeypatch):
    catalog = [{
        "id": "land-nav",
        "title": "USMC Land Navigation",
        "filename": "land-navigation.pdf",
        "status": "ready",
        "page_count": 41,
        "chunk_count": 9,
        "ocr_status": "not_needed",
        "needs_attention": False,
    }]
    calls = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"books": catalog}

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, headers):
            calls.append((url, headers))
            return _Response()

    class _SearchRag:
        healthy = True

        def search(self, query, k, owner):
            assert query == "terrain association"
            assert owner == "leo"
            assert k == 12
            return [{
                "document": "Terrain association uses visible features to maintain position.",
                "metadata": {
                    "owner": "leo",
                    "library": "books",
                    "book_id": "land-nav",
                    "filename": "land-navigation.pdf",
                    "page": 17,
                    "chunk_id": 3,
                },
                "similarity": 0.91,
            }, {
                "document": "unrelated private document",
                "metadata": {"owner": "leo", "library": "documents"},
                "similarity": 0.99,
            }]

    monkeypatch.setattr("httpx.AsyncClient", _Client)
    monkeypatch.setattr("src.rag_singleton.get_rag_manager", lambda: _SearchRag())

    result = asyncio.run(do_manage_books(json.dumps({
        "action": "search",
        "query": "terrain association",
        "limit": 3,
    }), owner="leo"))

    assert result["exit_code"] == 0
    assert result["count"] == 1
    assert result["results"][0]["title"] == "USMC Land Navigation"
    assert result["results"][0]["page"] == 17
    assert "/api/personal/books" in calls[0][0]
    assert "/srv/" not in result["output"]


def test_manage_books_list_keeps_model_payload_focused(monkeypatch):
    catalog = [{
        "id": "private-catalog-id",
        "title": "USMC Land Navigation",
        "filename": "land-navigation.pdf",
        "status": "ready",
        "page_count": 41,
        "chunk_count": 9,
        "ocr_status": "not_needed",
        "needs_attention": False,
        "updated_at": "2026-09-02T01:00:00Z",
        "source": "/private/owner/path/land-navigation.pdf",
    }]

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"books": catalog}

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, headers):
            return _Response()

    monkeypatch.setattr("httpx.AsyncClient", _Client)
    result = asyncio.run(do_manage_books('{"action":"list"}', owner="leo"))

    assert result["books"] == catalog
    assert "USMC Land Navigation" in result["output"]
    assert "private-catalog-id" not in result["output"]
    assert "updated_at" not in result["output"]
    assert "/private/owner/path" not in result["output"]
