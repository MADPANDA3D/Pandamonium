# Robotics ebook inventory and deduplication audit — `jimmyval92/robotics_ebooks`

**Task:** MAD-794 · **Date:** 2026-09-14 · **Type:** read-only inventory/audit, **no import performed**

Pinned snapshot: commit `5441f772ac826b903eda1daf9a9e5adf0e6c3ac2`
(tree `ab976a028059075b6ba607cf94350a6f484b03f7`), repo default branch `master`,
last pushed 2019-09-03, commit message `First commit` by `listofbanned`.

## Verdict for Leo

| Decision | Count |
|---|---|
| Approve for import / redistribution | **0** |
| Skip (unknown copyright/license) | **107** |

- **Reproduced exactly:** 107 PDFs totalling **1,790,948,281 bytes**, matching the prior
  audit with no discrepancy. Count and byte total are asserted by `--verify`.
- **License:** the repository declares **no license** (GitHub repo metadata `license` is
  null at audit time; the pinned tree contains no LICENSE/COPYING/NOTICE/UNLICENSE file).
  Unknown is never approved for redistribution, so **everything is skip**.
- **Duplicates:** 0 exact duplicates (every file has a unique git blob SHA-1 *and* a
  unique byte size) and 0 probable duplicates by normalized title. Two near-title
  candidates were reviewed and are distinct volumes. No pruning is needed.
- **Classification:** encryption, structural corruption, and text-native vs scanned are
  **not determinable from tree metadata alone**, and this audit deliberately did not
  fetch PDF bytes. The artifact carries low-confidence size-bucket estimates only.
- **Also upstream, outside the PDF scope:** 4 DJVU ebooks (+ README) — same unknown
  license, same skip recommendation.

## Artifacts

| Path | What it is |
|---|---|
| `docs/inventory/robotics_ebooks_inventory.json` | Full per-file inventory (JSON, schema `pandamonium.books.inventory/1`) |
| `docs/inventory/robotics_ebooks_inventory.csv` | Same rows as a spreadsheet-friendly CSV |
| `docs/inventory/robotics_ebooks_tree_ab976a028059075b6ba607cf94350a6f484b03f7.json` | Raw pinned GitHub tree API response (evidence) |
| `scripts/inventory_robotics_ebooks.py` | Reproducible generator + `--verify` + optional `--library-dir` fingerprinting |

Per file the artifact records: path, derived title, normalized title, size, git blob
SHA-1 (upstream object identity), blob API URL, raw URL, license status, import
recommendation, duplicate relations, classification, and OCR/language estimates.

**Hash method.** The GitHub tree API exposes the git object id (SHA-1, 40 hex chars)
for every blob. It does not expose SHA-256 at this commit, so the artifact records
`git_blob_sha1` plus the acquisition check: after downloading, `git hash-object <file>`
must equal `git_blob_sha1` and `wc -c <file>` must equal `size_bytes`. No SHA-256 can
be known before acquisition without downloading bytes, and none was invented.

## Verification (reproducible)

```console
$ python3 scripts/inventory_robotics_ebooks.py --verify
[PASS] tree from github_api: 112 entries, 107 PDFs, 1790948281 bytes
[PASS] artifact rows match tree (107 files)
[PASS] artifact totals reproduce (1790948281 bytes)
[PASS] duplicate analysis reproduces
[PASS] CSV row parity (107)
[PASS] license policy: status=unknown, 0 approved, all skip
[PASS] no tracked PDFs in git index
VERIFY PASS

$ python3 scripts/inventory_robotics_ebooks.py --verify --offline   # from the committed cache
VERIFY PASS
```

Regeneration (network: GitHub tree API metadata only, no PDF bytes):

```console
$ python3 scripts/inventory_robotics_ebooks.py
PDFs=107 bytes=1790948281 exact_dup_groups=0 probable_dup_groups=0 approved=0 skip=107
```

Single-file acquisition check (run only on a copy you are entitled to obtain; this
audit did not download anything):

```console
$ curl -L --fail -o /tmp/candidate.pdf "<raw_url from the artifact>"
$ git hash-object /tmp/candidate.pdf   # must equal git_blob_sha1
$ wc -c < /tmp/candidate.pdf           # must equal size_bytes
$ pdfinfo /tmp/candidate.pdf && pdffonts /tmp/candidate.pdf   # real text/scan/encryption check
```

## Duplicate analysis

Method, in order of strength:

1. **Exact:** group by git blob SHA-1 (byte identity). Result: **0 groups**.
2. **Exact by size:** group by byte size. Result: **0 collisions** — an independent
   second signal that no two PDFs are byte-identical.
3. **Probable:** group by normalized title (case-folded, diacritics/punctuation/
   dashes removed, whitespace collapsed). Result: **0 groups**.
4. **Near-title review:** `difflib` ratio ≥ 0.82 across normalized titles, reviewed
   manually because series volumes can look alike:

| Pair | Ratio | Review |
|---|---|---|
| `The MEMS Handbook MEMS Applications (2nd Ed)` vs `The MEMS Handbook MEMS Design (2nd Ed)` | 0.865 | distinct volumes of the same handbook |
| `CRC Press - Opto-Mechatronic Systems Handbook` vs `CRC Press - The Mechatronics Handbook` | 0.846 | different books |
| `Robot Builders Bonanza` vs `Robot Builders Source Book` (0.816, below threshold) | 0.816 | different books by the same author |

Cross-format check: none of the 4 upstream DJVU titles normalize to any PDF title.
**Conclusion: no exact or probable duplicates; nothing to prune.**

## License / copyright status

- `GET https://api.github.com/repos/jimmyval92/robotics_ebooks` → `license: null`
  (observed 2026-09-14; the repo description is just "A collection of robotics ebooks.").
- Pinned tree contains no `LICENSE`, `COPYING`, `NOTICE`, or `UNLICENSE` file.
- Consequently **every file** carries `license_status: "unknown"`,
  `redistribution_approved: false`, `import_recommendation: "skip"`, reason
  `unknown copyright/license status; do not redistribute or import`.
- Per-title copyright was **not** assessed (no rights database was queried for this
  audit). If Leo wants any individual title for private study, its rights must be
  verified independently before it is acquired, and it still must not be redistributed
  with Pandamonium or placed in any shared/synced library.

## Classification and OCR estimate

Determinable from the tree metadata alone:

- every entry is a `blob`, mode `100644`, `.pdf` extension, non-zero size → coherent
  PDF candidates (107/107);
- exact/size duplicate relations (above);
- all 4 DJVU entries and README.md are outside the 107-PDF scope.

**Not determinable without PDF bytes** (recorded per file as
`not_determinable_from_tree_metadata`): `pdf_magic_header`, `structural_integrity`,
`encryption`, `text_native_vs_scanned`, `page_count`. No per-file "corrupt",
"encrypted", or "scanned" claim is made.

Low-confidence estimates, clearly labeled as estimates in the artifact
(`ocr_need_estimate.confidence: "low"`, basis: blob size only):

| Estimate | Rule | Count |
|---|---|---|
| `likely_text_native` | < 2 MiB | 6 |
| `unknown` | 2–32 MiB | 86 |
| `likely_scanned_or_image_heavy` | ≥ 32 MiB | 15 |

These are triage hints, not results. Real classification requires acquiring the file and
running `pdfinfo` / `pdffonts` / `qpdf --check` (or OCR), as shown above.

**Language:** all 107 titles are English text; recorded as `en` with low confidence and
basis "filename/title only; file contents not inspected".

## Current Pandamonium Books library dedupe

The live Books catalog was **not discoverable in this worktree/data**, and the CT103
deployment was deliberately not touched. Checks performed:

- repo-wide search for a Books catalog/library file: none tracked;
- `PERSONAL_UPLOADS_DIR` (`<app_root>/data/personal_uploads`) existence check: absent;
- `~/.local/share`, `~/.config`, and canonical checkout scans for `catalog.json`:
  none found.

**Not checked:** the live Pandamonium Books catalog on CT103, and PDF content beyond
tree metadata. To run the real comparison later (read-only, local only):

```console
$ python3 scripts/inventory_robotics_ebooks.py \
    --library-dir /path/to/personal_uploads/<owner>/books \
    --library-out /tmp/robotics_library_dedupe.json
```

That scan computes SHA-256 and git blob SHA-1 locally per library PDF and reports exact
matches by blob id, then filename, then normalized title, then size-only. It does not
modify the committed artifact, and it never copies or uploads a file.

## Approve / skip highlights for Leo

- **Approve: none (0/107).** The source has no license, so nothing can be imported or
  redistributed as-is.
- **Skip: all (107/107).** Exact list with per-file reasons is in the JSON/CSV.
- Largest items (bandwidth/triage): `Field and Service Robotics - Corke` (82.1 MiB),
  `The MEMS Handbook (1st Ed)` (66.9 MiB), `Field and Service Robotics- Recent
  Advances` (65.1 MiB), `Neurotechnology for Biomimetic Robots` (65.0 MiB),
  `CNC Robotics` (55.8 MiB).
- Smallest items: `Efficient Collision Detection for Animation and Robotics` (0.80 MiB),
  `CRC Press - Mechanical Engineering Handbook - Robotics` (1.55 MiB),
  `Concise Encyclopedia of Robotics` (1.71 MiB).
- Upstream extras outside the PDF count: `Introduction to Robotics Mechanics and
  Control - J J Craig.djvu`, `Introductory Robotics - J M Selig.djvu`,
  `Practical and Experimental Robotics - Ferat Sahin & Pushkin Kachroo.djvu`,
  `Vision And Action - The Control of Grasping - Goodale M.A.(Ed).djvu` — same unknown
  license, also skip.

## Limitations

- No PDF bytes were fetched, so encryption/corruption/text-nativeness/page count are
  unknown, by design.
- No SHA-256 is available pre-acquisition for this SHA-1 repository; git blob SHA-1 is
  the pinned identity.
- Near-duplicates and OCR estimates are heuristic and labeled as such.
- Per-title copyright status was not researched.
- The live CT103 Books catalog was not compared (out of scope for this lane).