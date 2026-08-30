import pytest

from src.knowledge_source_policy import exclusion_reason, source_record, validate_wiki_ingest


def test_source_policy_excludes_dependency_and_secret_paths(tmp_path):
    dependency = tmp_path / "project" / "node_modules" / "package" / "README.md"
    dependency.parent.mkdir(parents=True)
    dependency.write_text("dependency", encoding="utf-8")
    secret = tmp_path / "private.key"
    secret.write_text("secret", encoding="utf-8")

    assert exclusion_reason(dependency, root=tmp_path) == "excluded_path:node_modules"
    assert exclusion_reason(secret, root=tmp_path) == "secret_filetype"


def test_canonical_source_record_carries_hash_time_class_and_scope(tmp_path):
    document = tmp_path / "docs" / "architecture.md"
    document.parent.mkdir()
    document.write_text("# Architecture", encoding="utf-8")

    record = source_record(document, root=tmp_path, owner="leo")

    assert record["document_class"] == "canonical_document"
    assert len(record["content_hash"]) == 64
    assert record["modified_time"] > 0
    assert record["owner"] == "leo"
    assert record["visibility"] == "owner"
    assert record["indexable"] is True


def test_generated_wiki_requires_sources_and_version_and_rejects_dependency_lineage(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    good = wiki / "good.md"
    good.write_text(
        '---\ntype: concept\nsources: ["[[docs/source]]"]\n---\nBody',
        encoding="utf-8",
    )
    polluted = wiki / "polluted.md"
    polluted.write_text(
        '---\ntype: concept\nsources: ["[[project/node_modules/pkg/README]]"]\n---\nBody',
        encoding="utf-8",
    )

    good_record = source_record(
        good,
        root=tmp_path,
        wiki_root=wiki,
        generation_version="1.22.1",
    )
    polluted_record = source_record(
        polluted,
        root=tmp_path,
        wiki_root=wiki,
        generation_version="1.22.1",
    )

    assert good_record["document_class"] == "generated_wiki"
    assert good_record["source_links"] == ["docs/source"]
    assert good_record["indexable"] is True
    assert polluted_record["indexable"] is False
    assert polluted_record["exclusion_reason"] == "wiki_excluded_source"


def test_wiki_ingest_fails_closed_on_missing_or_excluded_lineage():
    with pytest.raises(ValueError, match="wiki_missing_sources"):
        validate_wiki_ingest({"domain": "wiki", "authority": "secondary"})
    with pytest.raises(ValueError, match="wiki_excluded_source"):
        validate_wiki_ingest({
            "domain": "wiki",
            "authority": "secondary",
            "source_links": ["project/node_modules/pkg/README.md"],
            "generation_version": "1.22.1",
        })

    validate_wiki_ingest({
        "domain": "wiki",
        "authority": "secondary",
        "source_links": ["docs/architecture.md"],
        "generation_version": "1.22.1",
    })
