# Reviewed Datasets and Observable Training Jobs

**Linear:** `MAD-797` · builds on the MAD-796 external runtime adapter
([`docs/unsloth-runtime.md`](./unsloth-runtime.md)).

MAD-796 made training an optional external runtime and explicitly deferred job
submission. MAD-797 adds the governed path: a dataset manifest where every item
is explicitly selected, screened, reviewed, and fingerprinted; and training jobs
that are previewed, explicitly confirmed, observable (progress / metrics /
checkpoints / logs), cancellable with an explicit retention choice, and
resumable from a retained checkpoint.

No real GPU job is authorized by this work. Everything below is fixture-proven
against mocked transports; the operator-side procedure is at the end.

## 1. Dataset manifests (`src/training_datasets.py`)

Datasets are installation-level (`owner IS NULL`) and admin-configured. A
dataset starts empty. There is **no scan, walk, or auto-selection** anywhere:
the only way an item enters a dataset is an explicit `POST /api/training/
datasets/{id}/items` call naming one source.

Every item records:

| Field | Meaning |
| --- | --- |
| `source_kind` | `manual`, `file`, `book`, `conversation`, `memory`, or `tool_trace` |
| `source_ref` | explicit provenance (path, book id, session/message ids, memory ids, run id) |
| `owner` | the source row's owner (or the acting operator for `manual`) |
| `consent_license` | operator attestation, required on every item |
| `consent_attested_by` | the acting operator |
| `review_state` | `pending`, `approved`, `rejected`, `redacted`, or `excluded` |
| `content_hash` | SHA-256 over the exact stored snapshot |
| `redaction` | per-category screening report (counts only, never values) |
| `exclusion_path` | exact copy of how to remove this item; the source is never modified |

Content is a bounded snapshot (≤64k chars) stored Fernet-encrypted at rest
(`EncryptedText`), because it can hold private conversation or book text.

### Opt-in sources

| `source_kind` | How it is selected | Notes |
| --- | --- | --- |
| `manual` | inline `content` in the request | operator-pasted text |
| `file` | `source_ref.path` | must be inside a root from `ODYSSEUS_TRAINING_FILE_ROOTS` (JSON array of absolute paths, ≤16); no default roots |
| `book` | `source_ref.book_id` | resolved from the operator's Books catalog; PDF text via pypdf, Office/EPUB via markitdown |
| `conversation` | `session_id` + optional `message_ids` | only the named messages; owner comes from the session |
| `memory` | `memory_ids` | only the named memory rows |
| `tool_trace` | `run_id` | one scheduled-task run's `steps` JSON |

### Screening (reject or redact before submission)

`screen_content()` runs at add time and again on every approval:

* **Rejected** (no content stored, only the reason): private-key blocks, AWS
  keys, GitHub/Slack tokens, `password/api_key/token/secret = ...`
  assignments, SSN-shaped numbers, payment-card-shaped numbers.
* **Redacted** (stored redacted, counts in the report): email addresses and
  phone numbers, plus the secret-shaped text patterns from
  `src/authority_protocol.redact_secret_text`.

A rejected item cannot be approved. If a tampered item would now fail
screening, approval is refused and the item is marked rejected instead.

### Review and fingerprint

* `POST .../review` fails while any item is still `pending`.
* The dataset fingerprint is `sha256` over `"{item_id}:{content_hash}"` for all
  `approved`/`redacted` items, ordered by item id. It is the exact thing a job
  preview/start pins.
* Any add/remove/review resets the dataset to `draft` and clears the
  fingerprint, so a changed dataset can never ride an old review.
* `verified_dataset()` re-verifies the fingerprint at preview and start time
  and fails closed with `dataset_changed`.

### Exclusion / deletion path

`DELETE /api/training/datasets/{id}/items/{item_id}` removes the manifest item
and returns `source_modified: false`. Sources (conversations, memories, files,
books, tool traces) are never touched. Soft exclusion (`decision: exclude`) and
dataset retirement (`POST .../retire`) are also available.

## 2. Job preview (`src/training_jobs.py`)

`POST /api/training/jobs/preview` makes **no HTTP call** and returns:

* model, method (`qlora` / `lora` / `full`), and the VRAM class for each method;
* the dataset summary plus its fingerprint and item count;
* bounded, whitelisted training parameters (unknown keys are rejected);
* output location and runtime-side dataset path (both operator-provided — no
  baked paths);
* resource estimate: dataset items/chars plus the runtime's last recorded
  resource summary (`unknown` when the runtime has not reported it);
* **implications**: destructive (writes/replaces files under the output
  location; stop without retention discards partial progress), paid (runtime
  operator's billing terms apply; Pandamonium does not meter charges), data
  egress (item count + fingerprint become readable by the runtime), retention;
* `confirm_fingerprint` (reuses `src.authority_protocol.argument_fingerprint`
  over the exact spec) and an `expires_at` (30 minutes).

## 3. Job start (explicit authorized action)

`POST /api/training/jobs` requires all of:

1. admin authorization (`require_admin` — the same gate as the runtime tab);
2. a reviewed dataset whose fingerprint still verifies;
3. `confirm_fingerprint` equal to a fresh preview's fingerprint (a changed spec
   fails with `preview_mismatch`; there is no path that starts training without
   a preview);
4. `acknowledge_implications: true` (destructive/paid acknowledgment);
5. a runtime whose cached discovery reports `training: supported`;
6. no other active job (`job_active`).

Only then does `src/training_jobs.py` call the adapter's
`start_training_job()` — the single POST path added in MAD-797. Health,
discovery, and connection-test calls remain structurally GET-only, and
`tests/test_training_jobs.py` records every request to prove a connection test
never POSTs.

### Runtime contract assumptions (job control)

The adapter now speaks these documented-Studio-shaped routes; unknown responses
fail closed:

| Method | Route | Body / use |
| --- | --- | --- |
| `POST` | `/api/train/start` | `{model, method, params, dataset{id,name,fingerprint,item_count,path}, output_dir, resume_from_checkpoint?}`; response `{job_id, state}` |
| `POST` | `/api/train/stop` | `{"save": bool}`; `save` decides whether a checkpoint survives |
| `GET` | `/api/train/status` | state/progress/checkpoints/logs/metrics |
| `GET` | `/api/train/metrics` | optional metrics read (404 tolerated) |

The dataset file itself is not uploaded: `export_dataset_jsonl()` writes the
approved manifest to `DATA_DIR/training_datasets/<id>/dataset.jsonl` (mode
0600) and returns its SHA-256; the operator places it at the runtime-side
`dataset_path` (shared mount). Upload/materialization to the runtime is a
follow-up.

## 4. Lifecycle and observability

States: `queued` → `running` → `checkpointing` → `completed` | `failed` |
`canceled`. `GET /api/training/jobs/{id}` returns progress (percent, step,
epoch, loss, ETA), metrics, checkpoints, a bounded redacted log tail, retained
artifacts, cancel state, and the original implications.

* `POST .../refresh` re-reads the runtime (status + optional metrics) and
  updates the row; active jobs left in the DB are re-read by
  `POST /api/training/jobs/reconnect` (this is the restart/reconnect path).
* `POST .../cancel` with `retain_checkpoint: false` stops the job and clears
  `retained_artifacts`; with `true` the checkpoint list is preserved. Only the
  explicitly retained list is resumable: a canceled job with no retention has
  nothing to resume (`no_checkpoint`).
* `POST .../resume` requires a canceled/failed job with a retained checkpoint,
  a fresh preview fingerprint, and the implications acknowledgment; it starts a
  new job with `resume_of` set and `resume_from_checkpoint` in the spec.

## 5. Routes

| Route | Purpose |
| --- | --- |
| `GET/POST /api/training/datasets` | list / create |
| `GET /api/training/datasets/{id}` | manifest + items (no raw content; short previews) |
| `POST /api/training/datasets/{id}/items` | add one explicit item |
| `POST /api/training/datasets/{id}/items/{item_id}/review` | approve / reject / exclude |
| `DELETE /api/training/datasets/{id}/items/{item_id}` | exclusion/deletion path |
| `POST /api/training/datasets/{id}/review` | review + fingerprint |
| `POST /api/training/datasets/{id}/retire` | retire |
| `POST /api/training/datasets/{id}/export` | write the JSONL export |
| `POST /api/training/jobs/preview` | build the confirmed preview |
| `POST /api/training/jobs` | start (all gates above) |
| `GET /api/training/jobs` · `GET /api/training/jobs/{id}` | observe |
| `POST /api/training/jobs/{id}/refresh` · `POST /api/training/jobs/reconnect` | re-read runtime |
| `POST /api/training/jobs/{id}/cancel` · `POST /api/training/jobs/{id}/resume` | cancel / resume |

## 6. Limits and rollback

* 64k chars per item, 64k bytes per file, 200 messages/memories per item,
  1000 items per dataset, 40 params per job, 50 checkpoints listed, 8k log tail.
* Unknown params, unknown sources, out-of-bounds values, and unreviewed
  datasets all fail closed with named error codes.
* Rollback: cancel the job (`retain_checkpoint: false`), disable the runtime
  (Settings → Training Runtime), and delete only the dataset items/export you
  created. Source conversations, memories, files, books, and tool traces are
  never modified by this feature.

## 7. Operator-side verification (no GPU job authorized here)

Fixture coverage: success, runtime rejection, resource failure, cancellation
(with and without retention), reconnect, and resume, all through
`httpx.MockTransport`. To verify against a real runtime on an isolated machine:

1. Configure and Test the runtime in Settings → Training Runtime (MAD-796).
2. Create a small non-sensitive dataset: add one `manual` item, approve it,
   `POST .../review`, then `preview`.
3. Confirm the preview shows the fingerprint, output location, resources, and
   implications; confirm the start request fails without the acknowledgment.
4. Start the tiny job only after operator review; observe `refresh` until a
   terminal state; cancel once and confirm the retention behavior matches the
   `save` flag you chose.
5. Disable the runtime and remove the fixture dataset when finished.

## 8. Remaining (not in this slice)

* Settings-tab UI for datasets/jobs (the routes and preview/observability
  payloads are ready for it).
* Dataset upload/materialization to the runtime host (operator copies the JSONL
  export today).
* SSE log streaming (the API exposes a bounded log tail).
* Per-user datasets (installation-level admin scope today).