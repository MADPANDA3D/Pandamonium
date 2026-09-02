# routes/personal_routes.py
"""Routes for personal documents management."""
import os
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File, Depends
from src.request_models import DirectoryRequest
from core.constants import BASE_DIR, PERSONAL_DIR, PERSONAL_UPLOADS_DIR
from src.rag_singleton import get_rag_manager
from src.auth_helpers import require_privilege, require_user
from core.middleware import require_admin
from src.upload_handler import secure_filename
from src.upload_limits import PERSONAL_UPLOAD_MAX_BYTES
from src.personal_docs import extract_pdf_pages, ocr_pdf_pages

UPLOADS_DIR = PERSONAL_UPLOADS_DIR

logger = logging.getLogger(__name__)


def _personal_upload_dir_for_owner(owner: str | None, *, create: bool = True) -> str:
    """Return the per-owner upload directory used for direct RAG uploads."""
    owner_segment = secure_filename((owner or "local").strip())[:80] or "local"
    upload_dir = os.path.abspath(os.path.join(UPLOADS_DIR, owner_segment))
    base_abs = os.path.abspath(UPLOADS_DIR)
    if os.path.commonpath([upload_dir, base_abs]) != base_abs:
        raise ValueError("Unsafe upload owner path")
    if create:
        os.makedirs(upload_dir, exist_ok=True)
    return upload_dir


def _books_dir_for_owner(owner: str | None, *, create: bool = True) -> str:
    owner_dir = _personal_upload_dir_for_owner(owner, create=create)
    books_dir = os.path.abspath(os.path.join(owner_dir, "books"))
    if os.path.commonpath([books_dir, owner_dir]) != owner_dir:
        raise ValueError("Unsafe books path")
    if create:
        os.makedirs(books_dir, exist_ok=True)
    return books_dir


def _book_catalog_path(owner: str | None, *, create: bool = True) -> str:
    return os.path.join(_books_dir_for_owner(owner, create=create), "catalog.json")


def _load_book_catalog(owner: str | None) -> List[Dict[str, Any]]:
    path = _book_catalog_path(owner, create=False)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as catalog_file:
            payload = json.load(catalog_file)
        books = payload.get("books", []) if isinstance(payload, dict) else []
        return [dict(book) for book in books if isinstance(book, dict)]
    except (OSError, ValueError) as e:
        logger.error("Failed to read books catalog for %s: %s", owner, e)
        return []


def _save_book_catalog(owner: str | None, books: List[Dict[str, Any]]) -> None:
    path = _book_catalog_path(owner)
    temp_path = f"{path}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as catalog_file:
            json.dump({"version": 1, "books": books}, catalog_file, indent=2)
            catalog_file.flush()
            os.fsync(catalog_file.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass


def _public_book(book: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: book.get(key)
        for key in (
            "id", "title", "filename", "size_bytes", "page_count", "status",
            "chunk_count", "indexed_chunks", "ocr_status", "needs_attention",
            "attention_reason", "created_at", "updated_at",
        )
    }


def _book_source(owner: str, book: Dict[str, Any]) -> str:
    source = os.path.realpath(str(book.get("source") or ""))
    books_dir = os.path.realpath(_books_dir_for_owner(owner, create=False))
    try:
        allowed = source != books_dir and os.path.commonpath([source, books_dir]) == books_dir
    except ValueError:
        allowed = False
    if not allowed:
        raise HTTPException(409, "Book catalog source is invalid")
    return source


def _index_book(rag: Any, owner: str, book: Dict[str, Any], *, replace: bool = False) -> Dict[str, Any]:
    source = _book_source(owner, book)
    pages = extract_pdf_pages(source)
    ocr_status = "not_needed"
    if not any(page.strip() for page in pages):
        ocr_pages, ocr_status = ocr_pdf_pages(source)
        if ocr_pages:
            pages = ocr_pages

    book.update(
        page_count=len(pages),
        ocr_status=ocr_status,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    if not any(page.strip() for page in pages):
        book.update(
            status="needs_attention",
            chunk_count=0,
            indexed_chunks=0,
            needs_attention=True,
            attention_reason="needs_ocr" if ocr_status == "unavailable" else "ocr_failed",
        )
        return book

    documents = []
    for page_number, page in enumerate(pages, start=1):
        for page_chunk, chunk in enumerate(rag._split_into_chunks(page), start=1):
            documents.append((chunk, {
                "source": source,
                "source_id": f"book:{book['id']}:page:{page_number}:chunk:{page_chunk}",
                "filename": book["filename"],
                "stored_filename": os.path.basename(source),
                "directory": os.path.dirname(source),
                "type": ".pdf",
                "library": "books",
                "book_id": book["id"],
                "page": page_number,
                "chunk_id": len(documents),
                "owner": owner,
            }))

    if replace:
        rag.delete_by_source(source)
    result = rag.add_documents_batch(documents)
    if result.get("success"):
        book.update(
            status="ready",
            chunk_count=len(documents),
            indexed_chunks=result.get("added_count", len(documents)),
            needs_attention=False,
            attention_reason=None,
        )
    else:
        book.update(
            status="failed",
            chunk_count=len(documents),
            indexed_chunks=0,
            needs_attention=True,
            attention_reason="indexing_failed",
        )
    return book


def _unique_personal_upload_path(upload_dir: str, original_name: str | None) -> Tuple[str, str, str]:
    """Build a collision-resistant upload path while preserving a display name."""
    safe_name = secure_filename(os.path.basename(original_name or "upload"))
    if not safe_name or safe_name.startswith("."):
        safe_name = "upload"

    stem, ext = os.path.splitext(safe_name)
    stem = (stem or "upload")[:80]
    filename = f"{stem}-{uuid.uuid4().hex[:10]}{ext.lower()}"
    file_path = os.path.abspath(os.path.join(upload_dir, filename))
    upload_abs = os.path.abspath(upload_dir)
    if os.path.commonpath([file_path, upload_abs]) != upload_abs:
        raise ValueError("Unsafe upload filename")
    return file_path, filename, safe_name


def _unique_existing_target(path: str) -> str:
    """Return a non-existing sibling path for rename collision handling."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    while True:
        candidate = f"{stem}-{uuid.uuid4().hex[:10]}{ext}"
        if not os.path.exists(candidate):
            return candidate


def _remove_empty_tree(path: str) -> None:
    """Best-effort removal of empty directories under ``path``."""
    if not os.path.isdir(path):
        return
    for root, dirs, _files in os.walk(path, topdown=False):
        for dirname in dirs:
            candidate = os.path.join(root, dirname)
            try:
                os.rmdir(candidate)
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass


def rename_personal_upload_owner(
    old_owner: str,
    new_owner: str,
    *,
    personal_docs_manager: Any = None,
    rag_manager: Any = None,
) -> Dict[str, Any]:
    """Move direct personal uploads and rewrite RAG owner metadata on user rename."""
    old_dir = _personal_upload_dir_for_owner(old_owner, create=False)
    new_dir = _personal_upload_dir_for_owner(new_owner, create=False)
    path_map: Dict[str, str] = {}
    moved_files = 0

    if os.path.isdir(old_dir) and old_dir != new_dir:
        os.makedirs(new_dir, exist_ok=True)
        for root, _dirs, files in os.walk(old_dir):
            rel_root = os.path.relpath(root, old_dir)
            target_root = new_dir if rel_root == "." else os.path.join(new_dir, rel_root)
            os.makedirs(target_root, exist_ok=True)
            for filename in files:
                source = os.path.abspath(os.path.join(root, filename))
                target = _unique_existing_target(os.path.abspath(os.path.join(target_root, filename)))
                shutil.move(source, target)
                path_map[source] = target
                moved_files += 1
        _remove_empty_tree(old_dir)

    if personal_docs_manager is not None:
        rename_directory = getattr(personal_docs_manager, "rename_directory", None)
        if callable(rename_directory):
            rename_directory(old_dir, new_dir, path_map=path_map)

    books = _load_book_catalog(new_owner)
    catalog_changed = False
    for book in books:
        source = os.path.abspath(str(book.get("source") or ""))
        rewritten = path_map.get(source)
        if not rewritten and source.startswith(old_dir + os.sep):
            rewritten = new_dir + source[len(old_dir):]
        if rewritten and rewritten != source:
            book["source"] = rewritten
            catalog_changed = True
    if catalog_changed:
        _save_book_catalog(new_owner, books)

    rag_result = None
    if rag_manager is not None:
        rename_owner = getattr(rag_manager, "rename_owner", None)
        if callable(rename_owner):
            rag_result = rename_owner(
                old_owner,
                new_owner,
                path_map=path_map,
                path_prefixes=[(old_dir, new_dir)],
            )

    return {
        "old_dir": old_dir,
        "new_dir": new_dir,
        "moved_files": moved_files,
        "path_map": path_map,
        "rag_result": rag_result,
    }


def setup_personal_routes(personal_docs_manager, rag_manager, rag_available):
    """
    Setup personal documents related routes.

    Args:
        personal_docs_manager: PersonalDocsManager instance
        rag_manager: RAG manager instance (may be None)
        rag_available: Boolean indicating if RAG is available

    Returns:
        APIRouter instance with personal docs routes
    """
    router = APIRouter(prefix="/api/personal")

    def _rag():
        """Get the current RAG manager, retrying init if needed."""
        return get_rag_manager()

    def _resolve_allowed_personal_dir(directory: str) -> str:
        """Resolve a user-supplied personal-docs path under the allowed root."""
        if not directory:
            raise HTTPException(400, "Directory path is required")

        # realpath (not abspath) so a symlink inside PERSONAL_DIR that points
        # outside it is resolved before the commonpath confinement check below;
        # abspath only normalises `..` and would let such a symlink escape.
        base_abs = os.path.realpath(PERSONAL_DIR)
        candidate = directory if os.path.isabs(directory) else os.path.join(base_abs, directory)
        resolved = os.path.realpath(candidate)
        try:
            in_base = os.path.commonpath([resolved, base_abs]) == base_abs
        except ValueError:
            in_base = False
        if not in_base:
            raise HTTPException(403, "Directory must be inside personal documents")
        return resolved
    
    @router.get("")
    def api_personal_list(owner: str = Depends(require_user), _admin: None = Depends(require_admin)):
        """Enhanced version that includes directories"""
        files = [{"name": f["name"], "size": f["size"], "path": f.get("path", "")} for f in personal_docs_manager.index]
        directories = personal_docs_manager.get_indexed_directories() if hasattr(personal_docs_manager, "get_indexed_directories") else []
        return {"files": files, "directories": directories}
    
    @router.post("/reload")
    def api_personal_reload(owner: str = Depends(require_user), _admin: None = Depends(require_admin)):
        personal_docs_manager.refresh_index()
        return {"ok": True, "count": len(personal_docs_manager.index)}
    
    @router.post("/add_directory")
    async def add_directory_to_rag(
        request: Request,
        directory_request: DirectoryRequest,
        owner: str = Depends(require_user), _admin: None = Depends(require_admin),
    ):
        """
        Add a directory and all its subdirectories/files to the RAG index.
        
        Args:
            directory_request: Directory request model containing the directory path
            
        Returns:
            JSON response with indexing results
        """
        directory = directory_request.directory
        try:
            directory = _resolve_allowed_personal_dir(directory)
            
            # Security check - ensure directory exists and is accessible
            if not os.path.exists(directory):
                raise HTTPException(404, f"Directory not found: {directory}")
            
            if not os.path.isdir(directory):
                raise HTTPException(400, f"Path is not a directory: {directory}")
            
            logger.info(f"Adding directory to RAG: {directory}")
            
            # Use the RAGManager to index the directory
            rag = _rag()
            if rag:
                result = rag.index_personal_documents(directory, owner=owner)
                
                if result["success"]:
                    # Also update the personal_docs_manager to track this directory
                    personal_docs_manager.add_directory(directory, index=False)
                    
                    return {
                        "success": True,
                        "message": f"Successfully indexed {result['indexed_count']} chunks from {directory}",
                        "indexed_count": result["indexed_count"],
                        "failed_count": result.get("failed_count", 0),
                        "directory": directory
                    }
                else:
                    raise HTTPException(500, result.get("message", "Failed to index directory"))
            else:
                raise HTTPException(503, "RAG system is not available")
                
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error adding directory to RAG: {e}")
            raise HTTPException(500, f"Failed to add directory: {str(e)}")
    
    @router.delete("/remove_directory")
    async def remove_directory_from_rag(directory: str = Query(...), owner: str = Depends(require_user), _admin: None = Depends(require_admin)):
        """
        Remove a directory from the RAG index.

        Args:
            directory: Path to the directory to remove

        Returns:
            JSON response confirming removal
        """
        try:
            # Confine to PERSONAL_DIR — parity with add_directory_to_rag (which
            # resolves the path the same way). Without this, an arbitrary or
            # `..`-escaping path is passed straight to
            # personal_docs_manager.remove_directory / rag.remove_directory.
            directory = _resolve_allowed_personal_dir(directory)

            logger.info(f"Removing directory from RAG: {directory}")

            # Always remove from personal_docs_manager tracking
            if hasattr(personal_docs_manager, 'remove_directory'):
                personal_docs_manager.remove_directory(directory)

            # Remove from RAG vector store (best-effort)
            rag = _rag()
            if rag:
                try:
                    rag.remove_directory(directory)
                except Exception as e:
                    logger.warning(f"RAG removal failed for directory {directory}: {e}")

            return {
                "success": True,
                "message": f"Successfully removed {directory} from RAG index",
                "directory": directory
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error removing directory from RAG: {e}")
            raise HTTPException(500, f"Failed to remove directory: {str(e)}")
    
    @router.post("/upload")
    async def upload_files_to_rag(request: Request, files: List[UploadFile] = File(...)):
        """Upload files directly into RAG. Supports text and PDF."""
        user = require_privilege(request, "can_use_documents")
        rag = _rag()
        if not rag:
            raise HTTPException(503, "RAG system is not available — is the embedding service running?")

        collection = str(getattr(request, "query_params", {}).get("collection", "documents")).strip().lower()
        if collection not in {"documents", "books"}:
            raise HTTPException(400, "Unknown personal upload collection")
        is_books = collection == "books"
        upload_dir = _books_dir_for_owner(user) if is_books else _personal_upload_dir_for_owner(user)
        books = _load_book_catalog(user) if is_books else []

        total_indexed = 0
        total_failed = 0
        needs_attention = 0
        uploaded_files = []
        book_ids = []

        for upload in files:
            book = None
            try:
                file_path, stored_name, safe_name = _unique_personal_upload_path(upload_dir, upload.filename)
                content_bytes = await upload.read(PERSONAL_UPLOAD_MAX_BYTES + 1)
                if len(content_bytes) > PERSONAL_UPLOAD_MAX_BYTES:
                    logger.warning(f"Rejected oversized personal upload: {upload.filename!r}")
                    total_failed += 1
                    continue
                ext = os.path.splitext(safe_name)[1].lower()
                if is_books and (ext != ".pdf" or not content_bytes.startswith(b"%PDF-")):
                    total_failed += 1
                    continue
                with open(file_path, "wb") as f:
                    f.write(content_bytes)

                if is_books:
                    now = datetime.now(timezone.utc).isoformat()
                    book = {
                        "id": uuid.uuid4().hex,
                        "title": os.path.splitext(safe_name)[0],
                        "filename": safe_name,
                        "source": file_path,
                        "size_bytes": len(content_bytes),
                        "page_count": 0,
                        "status": "indexing",
                        "chunk_count": 0,
                        "indexed_chunks": 0,
                        "ocr_status": "pending",
                        "needs_attention": False,
                        "attention_reason": None,
                        "created_at": now,
                        "updated_at": now,
                    }
                    books.append(book)
                    _save_book_catalog(user, books)
                    _index_book(rag, user, book)
                    _save_book_catalog(user, books)
                    uploaded_files.append(safe_name)
                    book_ids.append(book["id"])
                    if book["status"] == "ready":
                        total_indexed += int(book["indexed_chunks"] or 0)
                    elif book["status"] == "needs_attention":
                        needs_attention += 1
                    else:
                        total_failed += 1
                    continue

                if ext == ".pdf":
                    from src.personal_docs import extract_pdf_text
                    text = extract_pdf_text(file_path)
                else:
                    text = content_bytes.decode("utf-8", errors="replace")

                if not text or not text.strip():
                    total_failed += 1
                    continue

                # Chunk and index
                chunks = rag._split_into_chunks(text, chunk_size=500)
                for i, chunk in enumerate(chunks):
                    metadata = {
                        "source": file_path,
                        "filename": safe_name,
                        "stored_filename": stored_name,
                        "directory": upload_dir,
                        "type": ext,
                        "chunk_id": i,
                    }
                    if user:
                        metadata["owner"] = user
                    if rag.add_document(chunk, metadata):
                        total_indexed += 1
                    else:
                        total_failed += 1

                uploaded_files.append(safe_name)
            except Exception as e:
                logger.error(f"Failed to upload/index {upload.filename}: {e}")
                if book is not None:
                    book.update(
                        status="failed",
                        needs_attention=True,
                        attention_reason="processing_failed",
                        updated_at=datetime.now(timezone.utc).isoformat(),
                    )
                    _save_book_catalog(user, books)
                total_failed += 1

        # Track uploads directory
        if uploaded_files and not is_books and hasattr(personal_docs_manager, "add_directory"):
            personal_docs_manager.add_directory(upload_dir, index=False)

        return {
            "success": True,
            "uploaded": uploaded_files,
            "book_ids": book_ids,
            "indexed_count": total_indexed,
            "failed_count": total_failed,
            "needs_attention": needs_attention,
        }

    @router.get("/books")
    def list_books(owner: str = Depends(require_user)):
        books = sorted(
            _load_book_catalog(owner),
            key=lambda book: str(book.get("created_at") or ""),
            reverse=True,
        )
        return {"books": [_public_book(book) for book in books]}

    @router.post("/books/{book_id}/reindex")
    def reindex_book(book_id: str, owner: str = Depends(require_user)):
        books = _load_book_catalog(owner)
        book = next((item for item in books if item.get("id") == book_id), None)
        if book is None:
            raise HTTPException(404, "Book not found")
        if not os.path.isfile(_book_source(owner, book)):
            raise HTTPException(404, "Book file not found")
        rag = _rag()
        if not rag:
            raise HTTPException(503, "RAG system is not available")
        book["status"] = "indexing"
        _save_book_catalog(owner, books)
        try:
            _index_book(rag, owner, book, replace=True)
        except Exception as e:
            logger.error("Failed to reindex book %s for %s: %s", book_id, owner, e)
            book.update(
                status="failed",
                needs_attention=True,
                attention_reason="processing_failed",
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            _save_book_catalog(owner, books)
            raise HTTPException(500, "Book reindex failed")
        _save_book_catalog(owner, books)
        return _public_book(book)

    @router.delete("/books/{book_id}")
    def delete_book(book_id: str, owner: str = Depends(require_user)):
        books = _load_book_catalog(owner)
        book = next((item for item in books if item.get("id") == book_id), None)
        if book is None:
            raise HTTPException(404, "Book not found")
        source = _book_source(owner, book)
        rag = _rag()
        removed_chunks = rag.delete_by_source(source) if rag else 0
        deleted_from_disk = False
        try:
            os.remove(source)
            deleted_from_disk = True
        except FileNotFoundError:
            deleted_from_disk = True
        books.remove(book)
        _save_book_catalog(owner, books)
        exclude_file = getattr(personal_docs_manager, "exclude_file", None)
        if callable(exclude_file):
            exclude_file(source)
        return {
            "success": True,
            "removed_chunks": removed_chunks,
            "deleted_from_disk": deleted_from_disk,
        }

    @router.delete("/file")
    async def delete_file_from_rag(filepath: str = Query(...), owner: str = Depends(require_user), _admin: None = Depends(require_admin)):
        """Delete a specific file from RAG index and optionally from disk."""
        try:
            # Remove chunks from RAG vector store (best-effort)
            removed = 0
            rag = _rag()
            if rag:
                try:
                    removed = rag.delete_by_source(filepath)
                except Exception as e:
                    logger.warning(f"RAG removal failed for {filepath}: {e}")

            # Delete file from disk if it's in the caller's own uploads dir.
            # Scope to the per-owner subdir, not the shared uploads root, so one
            # admin can't delete another user's personal files by path.
            deleted_from_disk = False
            try:
                abs_target = os.path.realpath(filepath)
                base_abs = os.path.realpath(_personal_upload_dir_for_owner(owner, create=False))
                in_uploads = (
                    abs_target == base_abs
                    or os.path.commonpath([abs_target, base_abs]) == base_abs
                )
            except ValueError:
                # commonpath raises on mixed drives / non-comparable paths
                in_uploads = False
            if in_uploads and abs_target != base_abs:
                try:
                    os.remove(abs_target)
                    deleted_from_disk = True
                except FileNotFoundError:
                    pass  # already gone — race with another request or cleanup

            # Exclude the file from the listing (persists across restarts)
            personal_docs_manager.exclude_file(filepath)

            return {
                "success": True,
                "removed_chunks": removed,
                "deleted_from_disk": deleted_from_disk,
            }
        except Exception as e:
            logger.error(f"Failed to delete file {filepath}: {e}")
            raise HTTPException(500, f"Failed to delete file: {str(e)}")

    return router
