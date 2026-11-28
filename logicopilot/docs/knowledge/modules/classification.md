---
title: Document classification
category: modules
module: classification
owner: Senthil
status: ready
summary: How every page of an incoming mail or upload is matched to a template document slot, what happens to pages nobody can place, and how a job is re-classified.
---
# Document classification

## What it does
A pre-alert usually arrives as a pile of files: invoices, packing lists, a waybill, a freight
certificate - sometimes all in one PDF, sometimes one file per document. Classification decides,
**page by page**, which **document slot** of the job's template each page belongs to (Invoice,
Packing List, Air Waybill, Freight Certificate …). Only after that can
[extraction](extraction.md) read the right fields from the right document.

If Cargora cannot place a page with confidence, the page goes to **Unclassified** on the job.
It is never thrown away. The operator drags it onto the right slot, and the classifier learns
from that.

## How it works
1. **Pick the classifier for the mode.** `classifier_module(group.mode)` loads one of four
   independent modules: Air Import, Sea Import, Air Export, Sea Export. A mode with no module of
   its own falls back to Sea Import.
2. **Drop junk pages.** Carrier terms-and-conditions pages are removed by fixed pattern rules
   (`page_filter.py`). Pages that match a Super Admin's **Filter Pages** reference page are
   removed too (`custom_page_filter.py`). Both are plain text comparison, not AI, so the result
   is the same on every run.
3. **Build the candidates.** Each template slot is described to the AI by:
   - its name and document type;
   - a built-in hint for that type (also recognised from the slot's name, even when misspelt -
     "Fright Certificate" counts as Freight);
   - its field list;
   - the text of the slot's sample document;
   - **classifier samples** learned from pages operators placed by hand.
4. **Ask the AI.** The reader depends on the platform's `extraction_engine` setting:
   - `ocr_gpt4o_mini` - the AI reads each file's OCR text (`classify_document`);
   - `gpt5_mini_vision` - the AI looks at the page images. With per-page vision, every page is
     judged on its own (`classify_pages_from_images`) in three passes:
     1. each page alone; the answer must clear the confidence bar;
     2. pages still unsure are asked again, told what the page before them turned out to be
        (this catches continuation pages);
     3. a keyword signature in the page's own text places a page that is still unplaced, or adds
        a second type to a page (an "invoice cum packing list").
   Every match must come with **evidence** - the words that decided it. A match without
   evidence is dropped.
5. **Weigh all files together.** `assign_documents_detailed` looks at every file of the mail or
   upload at once. A slot goes to the most specific claimant, so one invoice cannot fill both
   the Invoice and the Packing List slot.
6. **Combined documents.** A deterministic keyword check adds a second type the AI missed when
   the page plainly carries it (for example, "SHIPPING INVOICE CUM PACKING LIST").
7. **Place the pages.** Pages are split or merged into the job's documents. Several invoices in
   one file become several rows in the Invoice slot. A second copy of the same document (the
   same content arriving in two attachments) is skipped.
8. **Unclassified.** Every page is staged as an `UnmatchedUploadPage` **before** classification
   runs. Pages that end up placed are removed from that list, and the rest stay visible on the
   job. Unplaced Excel sheets are kept as `UnclassifiedFile` rows.
9. **Excel by sheet** templates classify each sheet from its cell text, **after** the PDFs and
   separately from them, so sheets never change where PDFs land (see [Email intake](email-intake.md)).

### Which customer? (shared inboxes only)
Before slot classification, a mailbox that serves several templates must first find the right
**template**. `identify_customer` gives the AI each template's identifying values (consignee,
importer, IE code, GSTIN, branch) and asks which one the documents name. The rules are strict:
- the documents decide, not the email wording or the sender address;
- a shipper or supplier name does not count;
- if nothing matches, the answer is empty;
- if several templates fit equally, all of them are returned.
In both of the last two cases the mail goes to **Pending Mail** for a person to decide. A
mailbox routed to one template skips this step.

### Fixing a wrong placement
- **Assign an Unclassified page** - the operator drags it onto a slot
  (`assign_unclassified_page`). The page becomes a real document of that slot, and its text is
  remembered as a classifier sample for that slot (`document_samples.remember`).
- **Move or upload into a slot** - uploading a file straight into a slot also teaches the
  classifier. Deleting a misfiled document and uploading it into the right slot is the
  strongest signal of all.
- **Re-classify documents** (Super Admin only, `reclassify_documents`) - runs classification
  again on the job's own files, as if they had just been uploaded. This is destructive: existing
  documents **and every operator correction** on the job are wiped, then extraction runs again.
  It is meant for recovery after a classifier fix, not for daily use. It is refused while the
  job is extracting.

## Business rules
| Rule | Why |
|---|---|
| Each mode has its own classifier module | Tuning Air Import can never break Sea Import - they are not the same code |
| A page with no OCR text is not sent to the AI to guess (text engine) | A guess with nothing to quote as evidence must not file a document |
| A match needs evidence | "Contains a packing list" is very different from "looks a bit like one" |
| Confidence bar for page images (`VISION_CLASSIFICATION_CONFIDENCE_THRESHOLD`, default 0.75) | Low-confidence pages go to Unclassified instead of the wrong slot |
| Keyword checks are per page, never per file | A whole-file check once put all 13 pages of an attachment into one slot |
| Unclassified pages are staged before classification | If classification fails half way, nothing disappears |
| Customer identification returns nothing rather than the closest match | A wrong match files a customs declaration against the wrong company |
| A freight forwarder's "Tax Invoice" for air freight charges (no goods lines) is Freight | It bills for the shipping service, not the cargo |
| Assigning an Unclassified page is not billed again | The page was billed when it was OCR'd |
| Re-classify is billed as a re-run | It sends pages through OCR/AI again |
| An AI outage is not recorded as "no match" | The mail is retried later instead of being lost |

## Where in the code
| What | Where |
|---|---|
| Module picker, AI-outage error, customer identification | `backend/app/core/classifier_common.py` - `classifier_module`, `AIServiceUnavailable`, `identify_customer` |
| Per-mode classifiers | `core/classifier_air_import.py`, `classifier_sea_import.py`, `classifier_air_export.py`, `classifier_sea_export.py` - `classify_document`, `classify_document_from_image`, `classify_page_image`, `classify_pages_from_images`, `assign_documents_detailed`, `_augment_combined_document_claims` |
| Learned samples | `core/document_samples.py` - `remember`, `for_slots` |
| Slot sample text | `core/email_puller.py` - `_slot_reference` |
| Terms pages / admin filter pages | `core/page_filter.py`, `core/custom_page_filter.py`; API `api/v1/custom_filter_pages.py` |
| Smart Upload / re-classify core | `api/v1/jobs.py` - `_classify_and_place_files`, `smart_upload`, `reclassify_documents` |
| Unclassified | `api/v1/jobs.py` - `assign_unclassified_page`, and the unclassified-files assign endpoint; models `UnmatchedUploadPage`, `UnclassifiedFile` in `models/job.py` |
| Debug tool | `api/v1/classifier_test.py` (Super Admin; uses the Sea Import module) |
| Screens | Filter Pages `pages/super-admin/FilterPagesPage.tsx`; classifier samples on Template Settings (Step 6) |

## Settings
- Platform: `extraction_engine` (`ocr_gpt4o_mini` or `gpt5_mini_vision`) - picks the reader for
  classification as well as extraction.
- Server (`.env`): `OPENAI_MODEL`, `VISION_ENGINE_MODEL`, `VISION_CLASSIFICATION_CONFIDENCE_THRESHOLD`,
  `OCR_ENGINE`, `EXTRACTION_WORKERS` (classification shares the extraction worker cap).
- Per template: the slots themselves (name, document type, required), slot samples, classifier
  samples (can be removed on Template Settings when one was learned from a wrong page).

## Design decisions
- **Four copies, not one shared classifier** - chosen for isolation between modes, at the cost
  of duplicated code. Only the code that runs before the mode is known is shared.
- **Learn from the operator's own placements** - wording alone hardly separates shipping
  documents, because they all quote each other. How one customer lays out their papers does
  separate them, and every manual placement is a free, correct label.
- **Classification shares the extraction worker cap** - on 2026-11-03, uncapped classification
  calls held database connections open and the site stopped answering.
