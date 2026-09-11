# MAD-842 — service discovery and work-budget follow-up

The operator compared two installed v1.0.42 turns using the same external-collection request. The 20-round default run made no tools calls. The 80-round Maximum run invoked `search_jarvis_knowledge` once with a generic overview query and returned ten internal-index records. Six citations in its answer matched those records. Neither turn reached its round ceiling. The successful-looking answer therefore did not demonstrate a read of the requested external collection.

`search_jarvis_knowledge` calls the internal knowledge store, whose domain search invokes Chroma. Its fallback is another internal RAG index. Neither path is a direct query of a user-named external collection. Both successful result shapes now include source provenance, and the function schema states the boundary.

MCP selection previously required overlap with the connection identity or catalog labels. A service hidden behind a broker need not match those fields. Unknown-domain first turns could also take a direct response path without tools, and local-model schema selection had another keyword gate. The shared fix offers only read-only entrypoints named in live initialize guidance, bounded to eight, keeps them through both gates and schema budgeting, and leaves explicit connection selection authoritative. It copies no service catalog and grants no execution permission beyond existing policy.

The operator authorized both this repair and the scale 20 / 40 / 80 / 120 / 200. The settings loader upgrades the old materialized 20 default to 80, preserves other custom values and stamps the new settings version when saving. An explicit saved 20 after upgrading remains 20. Native Codex reasoning is separate and unchanged.

Runnable verification:

- `tests/test_mcp_result_projection.py` exercises named-broker and service-only requests through local and API model paths. The fixture asserts mounted schemas on every model call and uses real MCP dispatch, authority decisions, argument validation, result formatting and context assembly. The local fixture uses a small reference for its context ceiling; the API fixture retains the existing oversized reference. Both execute a reference read, reject missing arguments, correct once and return a cited record.
- `tests/test_mcp_manager.py` checks discovery without service identity, declared read-only bounds and disconnected servers, alongside existing explicit-provider and authority tests.
- `tests/test_settings_store_shape.py` checks new default, legacy upgrade, custom caps and post-upgrade explicit 20.
- `tests/test_madpanda_knowledge.py` checks actual internal provenance.
- `tests/browser/m7-codex-workspace.spec.js` checks all five slider values and preserves native Codex behavior.

These fixtures establish host behavior, not a real model's reliability. Configured-runtime acceptance remains open until the operator installs the release and actual external-record and Discord reads are verified. No CT103 files or services are changed by this source repair.
