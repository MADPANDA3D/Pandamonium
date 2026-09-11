# MAD-842: installed MCP result projection regression

Diagnosis captured 2026-09-11 UTC. No runtime repair has been applied.
Governing issue: https://linear.app/madpanda3d/issue/MAD-842/integrations-make-mad-mcp-portal-tools-native-and-unify-connection
Evidence comment: `0fa711ac-6238-43b2-8289-a74488e5a626`.

## Reproduced boundary

CT103 installed v1.0.41 from protected release commit
`848b5155aebbfb22acec7b8fb00bf6953233b4eb`.
The MCP manager preserves structuredContent as structured_content, and the
action audit records it. The next model round receives format_tool_result's
text, whose generic extra-data branch slices pretty-printed JSON at 8,000
characters. Large JSON Schema definitions precede the actual input parameters.

Read-only replay used the installed formatter function and handled-key constant
extracted with Python AST, without importing the app or touching its database.
Stored tool results came from SQLite opened with mode=ro.

| Descriptor | Pretty-printed extra JSON | Truncated | Input query/collection_name/top_k visible |
| --- | ---: | --- | --- |
| qdrant-collection-info | 5,089 chars | no | collection_name visible |
| qdrant-get-points | 5,640 chars | no | applicable inputs visible |
| qdrant-build-context | 29,716 chars | yes | none |
| qdrant-find, all three attempts | 29,001 chars | yes | none |

The raw Find descriptor requires query and publishes collection_name and top_k.
The model later sent collection, filter and limit, without query. Re-requesting
the descriptor could not recover parameters removed by the same formatter.
This proves missing model input; it does not prove the model will always select
correct tools once the defect is repaired.

Git history locates the same 8,000-character slice in Odysseus v1.0 commit
e5c99a5e (2026-05-31). Existing MCP execution was present; large structured
result fidelity was incomplete.

## Exact recorded sessions

Database on CT103: /srv/odysseus/data/app.db, table chat_messages.
metadata.tool_events contains action_call and action_result; the original
MCP envelope is action_result.structured.structured_content.
These are private operational records. Do not commit raw messages or secrets.

- Overview session: b2d1155a-8a5b-4b95-8b74-3e62eb1bf31d.
  Assistant message: 4a13baa8-986e-4974-a514-07e05f4d118b.
  Collection Info succeeded. Build Context was wrongly gated as a credential
  change, then executed after approval. Find's invalid arguments stopped the run.
- Fresh targeted session: e0702d37-5906-4e3b-baa4-ab79d11686a8.
  Assistant message: fd5a1833-78c2-46ba-a13b-4fe96ee0c4c8.
  Only list_services and find_tools(query="jarvis-knowledgebase", limit=5)
  executed. The latter returned zero matches. No Qdrant content read occurred.
  The final paragraph calls its displayed record IDs illustrative placeholders;
  the answer's workflow and metadata/date claims are not retrieval evidence.

The token-budget false approval is separate: authority_protocol's recursive
_SECRET_KEY regex matches token anywhere in a key, including max_context_tokens.
This promotes a declared read to credential_or_auth_change and redacts the
budget. max_context_tokens is itself absent from the published Build Context
input schema; preserve validation as well as real credential protection.

## Comparison reads and limits

Independent Codex Portal calls read actual Qdrant content: 20 sampled points
from a 291-point collection using qdrant-list-points in pages of five, payload
included and vectors excluded. These were Codex reads, not Jarvis acceptance.
A page of 30 exceeded the Portal response envelope: originalBytes 189437,
maxBytes 57344. Smaller pages worked. Preserve provider pagination/size warnings.

## Repair boundary

Use the existing generic MCP client and shared callers:
src/mcp_manager.py, src/tool_execution.py, src/agent_loop.py,
src/action_protocol.py and src/authority_protocol.py. Trace them before edits.

- Keep complete executable schemas, required fields and referenced definitions
  visible to the model. Bound data responses by meaningful items/fields; retain
  IDs, citations, pagination and precise error/recovery information. Do not fix
  this by blindly raising every output limit or removing context budgets.
- Correct token-budget credential false positives while keeping actual secrets,
  credential changes, exact approval identity and audit redaction protected.
- Preserve provider argument-error details and at most one safe read correction.
  Do not retry writes, permission/transport failures or unknown outcomes.
- Regress the actual model-visible messages, not only the recorded catalog.
  Cover large schema, small result, oversize data, credential and empty-search cases.
- Retest targeted and browse prompts through Jarvis and preserve Discord success.
  Verify actual provider reads and returned citations. A plausible summary after
  discovery alone is not success. Reconcile the older empty-output/forced-synthesis
  failure if it recurs; do not claim the formatter fix automatically resolves it.

Pandamonium remains a generic MCP host. Portal owns provider catalogs, routing,
schemas and execution. No duplicated Portal registry, synthetic provider tools,
hardcoded service routing or keyword-based success detector.

Leo authorized scoped implementation and normal protected PR/release work in a
fresh task. CT103 installation remains Leo-owned under the governing issue;
read-only inspection is allowed. Friday gateway MAD-879 stays deferred.

