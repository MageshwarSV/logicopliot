---
title: AI extraction
category: modules
module: extraction
owner: Senthil
status: ready
summary: How the AI reads each document's fields and product lines, how computed fields follow, re-extraction, parallel extraction, operator corrections and the Document Processing page.
---
# AI extraction

## What it does
Once documents sit in their slots (see [classification](classification.md)), extraction reads
the template's **fields** out of each document:
- **header values** - BL number, invoice number, dates, weights, parties …;
- **product lines** - one row per item on an invoice or packing list.

Then **computed fields** are filled: fixed values, AI-calculated values, lookups (for example,
CTH from the customer's material master or the ICEGATE tariff) and combined fields (for example,
Combined Description). The operator then reviews everything on the job screen.

The AI model is a server setting (`OPENAI_MODEL`, and `VISION_ENGINE_MODEL` when the
platform's `extraction_engine` is `gpt5_mini_vision`). The Template List shows which model is
live.

## How it works
1. **Start.** Extraction starts on its own once every **required** slot is filled (`_maybe_auto_extract`).
   It does not start on its own if the template has *Start extraction automatically* off, or if
   the job is held for a large zip. Otherwise someone presses **Extract** / **Run extraction**
   (`extract_job`). A first manual Extract is refused while a required document is still missing.
2. **Queue.** The job is queued. Only a handful of extractions and classifications run at once
   (default 6, `EXTRACTION_WORKERS`). On a split setup, the web server hands the job to the
   worker server through Redis (`extraction_dispatch.py`).
3. **Page text.** Each document's OCR text is built. Carrier terms pages are dropped first.
4. **Header fields.** One AI call per document, with all its fields batched together
   (`extract_document_fields`, or `extract_document_fields_from_images` on the vision engine).
   Each field carries its own prompt, caption variations and format hint from the template, so a
   value captioned differently on a new layout is still found. Long documents are read in
   several overlapping chunks and the answers are merged.
5. **Product lines.** Fields ticked as *multiple values in this document* are read as a table
   in **one** call (`extract_document_rows` / `..._from_images`), so a row's description,
   quantity and price all come from the same printed row. When Document AI detects the table's
   row count, it is passed in as a hint. Rows from several chunks are added together, so seven
   invoices in one PDF keep all seven tables.
6. **Retry once if blank.** If every field of a document comes back empty, it is retried once,
   in case the AI call failed for a moment.
7. **Pairing.** Each invoice is paired with its own packing list by the invoice number printed
   on both, not by position (`doc_sets.py`).
8. **Line fallback.** A blank per-product value (for example, origin country) can be filled from
   another document's table, matched on the **part number**, never on row position
   (`line_fallback.py`).
9. **Computed fields** (`CustomField`) run after the document fields:
   - `hardcoded` - a fixed value;
   - `ai` - the AI computes it from chosen documents;
   - `lookup` - from the material master or the learned reference values;
   - `composite` - joined from other fields.
   Fields that depend on another field run after it, and composites run last. The ICEGATE
   customs tariff step runs if the template has it on.
10. **Corrections carried over.** Before the old values are cleared, operator corrections are
    saved. After the new values are written, each correction is put back on the same field and
    row.
11. **Done.** The job moves to *extracted*. The start and finish times are recorded for
    Production Support.

### Re-extract
- **Re-run extraction** (the job button) reads every document again.
- **Extract on one document's card** reads only that document (`only_job_document_id`). Other
  documents keep their values. Computed fields are always recomputed in full, because a computed
  field may depend on any document.
- A deliberate re-run is billed as a re-run. The first extraction is not billed separately,
  because the pages were already billed at OCR.

### Parallel extraction
`parallel_extract.py` speeds up a job without changing its results:
1. page OCR is fetched side by side;
2. every AI call the extraction loop is going to make is made in advance, side by side, and the
   answer is kept under the exact request;
3. the normal loop then runs unchanged and picks up the kept answers.

A request that differs from the plan simply makes the real call. With
`PARALLEL_EXTRACT_WORKERS` = 1 (the default) nothing is prefetched. Large-job-lane jobs use the
platform's `large_job_parallel_calls` instead. Computed-field AI calls can also run side by side
(`PARALLEL_COMPUTED_WORKERS`).

### Corrections and remembered values
- **Operator corrections** are stored next to the AI value (`corrected_value` vs
  `extracted_value`) and every edit is logged (`field_edits.record`).
- **Extraction Corrections** (Super Admin) lists the fields operators keep correcting on many
  different jobs (default: 3 or more). Its **Fix** screen does four things:
  - asks the extraction model to suggest a better prompt from the AI-vs-corrected values;
  - tests a candidate prompt on recent corrected jobs, read-only;
  - applies the prompt and keeps the old one in prompt history;
  - reverts to an earlier prompt.
- **Remembered values** (`CustomFieldReferenceValue`): when a lookup finds nothing and the
  operator types the answer, it is kept, so the same part or party is never typed twice. Fields
  marked *target value* replace the extracted value with the stored one when it matches.
  Matching is exact, or fuzzy only where the field allows it.

## Business rules
| Rule | Why |
|---|---|
| One AI call per document, fields batched | Cheap, and the values agree with each other |
| Table rows read in one call, arrays kept the same length | A row's values must come from the same printed row |
| Invoice and packing list paired by invoice number | Pairing by position would "verify" the wrong two documents |
| Line fallback matches on part number, never position | Position matching once nearly wrote the wrong origin country on a customs line |
| Operator corrections survive a re-extract | Re-running must not undo a person's work |
| Auto-extract fires only from a job's first "draft" state | Replacing one file on a finished job is a deliberate re-run, not automatic |
| Auto-extract waits only for required slots | Optional documents are never waited on |
| Extraction can be paused by a Super Admin (`extraction_paused`) | Stop AI spend at once; jobs stay ready to extract again later |
| Reference values match exactly by default | A near-miss on a material code or CTH is a wrong declaration |
| Prompt fixes are tested before they are applied, and can be reverted | A prompt change affects every future job of the template |

## Where in the code
| What | Where |
|---|---|
| Extraction calls | `backend/app/core/extraction.py` - `extract_document_fields`, `extract_document_rows`, `extract_document_fields_from_images`, `extract_document_rows_from_images`, `field_spec` |
| The run | `api/v1/jobs.py` - `run_extraction`, `extract_job`, `_maybe_auto_extract` |
| Queue / hand-off | `core/extraction_queue.py` (`run_queued`, `submit_extraction`), `core/extraction_dispatch.py`, `core/extract_queue.py` |
| Parallel | `core/parallel_extract.py` (`Prefetch`, `workers_for`, `prefetch_page_ocr`), `core/parallel_ocr.py` |
| Pairing / fallback | `core/doc_sets.py`, `core/line_fallback.py` (`apply_line_fallbacks`) |
| Excel sheets | `core/excel_text.py` (`sheet_row_chunks`) |
| Customs tariff step | `core/customs_tariff_jobs.py` (`apply_to_job`) |
| Corrections | `core/field_edits.py`, `core/prompt_fix.py`, `api/v1/extraction_corrections.py`; screens `pages/super-admin/ExtractionCorrectionsPage.tsx`, `FixPromptModal.tsx` |
| Remembered values | `core/reference_cache.py`, `models/custom_field_reference.py` |
| Large-job lane | `core/large_job_lane.py` (`request_extraction`) |
| Document Processing page | `frontend/src/pages/super-admin/DocumentProcessingPage.tsx`; API `template_groups.set_group_processing_settings` |

## Document Processing page
Super Admin > **Document Processing**: pick a template on the left and set, for every job of
that template (email and Smart Upload alike):
- **Start extraction automatically** (`auto_extract`);
- **Hold extraction for a large zip** and the size limit in MB (`large_zip_hold`, `large_zip_mb`, 1-500);
- **Use the large-job lane** (`large_job_lane`);
- **Read Excel by sheet** and the Excel ignore list (`excel_mode`, `excel_ignore_patterns`).

These settings are Super Admin only, because they change how every job of the template is read
and what it costs. The same settings also appear in Template Settings (Step 6) under
*Processing*.

## Settings
- Server (`.env`): `OPENAI_MODEL`, `VISION_ENGINE_MODEL`, `OCR_ENGINE`, `EXTRACTION_WORKERS`,
  `EXTRACT_QUEUE`, `PARALLEL_EXTRACT_WORKERS`, `PARALLEL_EXTRACT_MAX_TOTAL`,
  `PARALLEL_COMPUTED_WORKERS`, `PARALLEL_OCR_WORKERS`, `MONEY_CHECKS`.
- Platform: `extraction_engine`, `extraction_paused`, `large_job_parallel_calls`.
- Per template: the Document Processing settings above, plus each field's prompt, format hint and
  multi-value flag.

## Design decisions
- **Prefetch, don't rewrite the loop** - the loop shares one database session and is not safe to
  run in parallel. Only the AI calls run side by side, so the results are the same by construction.
- **Capped workers** - on 2026-11-03, uncapped extraction threads held database connections open
  while waiting on the AI, and the site went down. Extraction and classification now share one cap.
- **Re-extract one document** - re-reading all documents when one file changed was work nobody
  asked for.
