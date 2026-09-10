# Intelligent Document Extraction, Validation & API Platform

An end-to-end AI-powered platform that ingests real invoices and financial statements (PDF or image),
runs them through a layered OCR pipeline, extracts structured, evidence-grounded data, independently
recomputes the document's own arithmetic to check it for correctness, and serves everything through a
versioned REST API with a working dashboard on top.

Built for the "Intelligent Document Extraction, Validation & API Platform" AI Engineer internship case
study.

---

## 1. Problem Statement

Organizations receive large volumes of unstructured financial documents — invoices, balance sheets,
profit & loss statements, cash flow statements — as scanned PDFs or photographed images. Turning these
into structured, trustworthy data normally means manual data entry, which is slow and error-prone, and
naive OCR-to-JSON pipelines don't verify that the numbers they extracted are actually internally
consistent.

This platform automates the full chain: **validate the upload → extract text (native or OCR) →
extract structured fields and tables → independently verify the document's arithmetic → persist the
result → expose it over a REST API and a dashboard**, and it does this **without hardcoding anything
about the specific dataset** — the same code path processes any invoice, balance sheet, P&L, or cash
flow statement of the four supported types.

## 2. Solution Overview

- The user selects a document type (`invoice`, `balance_sheet`, `profit_and_loss`,
  `cash_flow_statement`) and uploads a PDF/JPG/PNG file (max 3 pages).
- The backend validates the file (type, integrity, page count) using magic-byte sniffing, not just the
  filename extension.
- Text is extracted natively from the PDF first (PyMuPDF); if that yields no usable text (a scanned
  page), the page is rendered to an image and OCR'd with Tesseract.
- A **deterministic, rule-based extraction engine** (regex + table-row reconstruction from OCR bounding
  boxes) turns the raw text into a structured, document-type-specific JSON shape, with every important
  field carrying `confidence`, `page_number`, and the literal `evidence` text it was read from.
- An **independent financial validation engine** re-derives the case study's accounting formulas
  (e.g. `Total Assets == Total Capital & Liabilities`, `Interest Earned + Other Income == Total Income`)
  from the extracted numbers and compares them against what the document itself reports, per
  comparative period, with a configurable tolerance.
- The result is persisted (SQLAlchemy, SQLite locally / Postgres-ready in production) and returned as
  one consistent JSON response shape regardless of document type.
- A small dashboard (Flask + vanilla HTML/CSS/JS) lets a reviewer upload documents, see PASS/FAILED
  status at a glance, and drill into any document's extracted fields, financial line items, validation
  checks, and raw JSON.
- An **optional** LLM-assisted extraction pass (Anthropic) can supplement (never override) the
  deterministic pass when an API key is configured — the system is fully functional without one.

## 3. Architecture

See [`docs/architecture.png`](docs/architecture.png).

```
Frontend (Flask)  ──fetch()──>  FastAPI backend
                                    │
                                    ▼
                         Document Validation Service
                        (extension, MIME sniff, integrity, page count)
                                    │
                                    ▼
                            OCR / Text Extraction
              PyMuPDF native text ──quality check──> Tesseract OCR fallback
                     (render @300dpi + bounding-box row reconstruction)
                                    │
                                    ▼
                       Structured Extraction Service
        (document-type-specific regex + table parsing → evidence-grounded JSON)
                                    │
                          (optional) LLM gap-fill
                                    │
                                    ▼
                       Financial Validation Service
              (recomputes formulas per period → PASS/FAIL/NOT_APPLICABLE)
                                    │
                                    ▼
                          Document Repository (SQLAlchemy)
                        SQLite (dev) / PostgreSQL (production)
                                    │
                                    ▼
                          Structured JSON API response
                                    │
                                    ▼
                     Dashboard + Document Result page + Swagger
```

## 4. Technology Stack

| Layer | Technology |
|---|---|
| Backend framework | FastAPI + Uvicorn |
| Validation/schemas | Pydantic v2 |
| Database ORM | SQLAlchemy 2.0 |
| Database | SQLite (local) / PostgreSQL-compatible (production) |
| Native PDF text | PyMuPDF (`fitz`) |
| OCR engine | Tesseract (via `pytesseract`) |
| Image handling | Pillow (incl. EXIF orientation correction) |
| Optional LLM | Anthropic Messages API (`anthropic` SDK) |
| Frontend | Flask + Jinja2 + vanilla HTML/CSS/JS (no frontend framework) |
| Testing | pytest, FastAPI `TestClient` |
| Containerization | Docker, docker-compose |

### 4.1 Why each technology

- **FastAPI**: native async support, automatic OpenAPI/Swagger docs (`/docs`) generated straight from
  the Pydantic models, first-class `multipart/form-data` handling for file uploads, and a clean
  dependency-injection model (used here for DB sessions).
- **PyMuPDF (`fitz`)**: fast, dependency-light native PDF text/page rendering; no external Poppler
  binary needed, which matters for a simple Docker/production deployment story.
- **Tesseract**: mature, free, open-source, fully local OCR engine — no per-page API cost, no external
  network dependency, and it exposes word-level bounding boxes (`image_to_data`), which this project
  relies on to reconstruct table rows (see §8).
- **SQLAlchemy**: the ORM abstracts SQLite vs. PostgreSQL behind one `DATABASE_URL` — switching database
  backends for production is a one-line environment variable change, no code change.
- **Pydantic**: guarantees the API's outer response shape is always consistent and self-documenting,
  while still allowing the inherently variable `extracted_data` payload to be a flexible dict.
- **Flask for the frontend**: the case study explicitly asks for simple HTML/CSS/JS, not a frontend
  framework; Flask is the smallest reasonable way to serve templates + static assets and inject the
  backend URL as configuration.
- **Anthropic SDK (optional)**: only used if `LLM_PROVIDER=anthropic` and `LLM_API_KEY` are set; the
  rule-based extraction engine is the real, tested, primary path (see §9 and §13).

## 5. Dataset Structure

```
dataset/
├── Balance Sheet/       10 scanned PDFs (2017-2026), 1 page each
├── Cash Flows/          10 scanned PDFs (2017-2026), 1-2 pages each
├── Profit & Loss/       10 scanned PDFs (2017-2026), 1 page each
└── Invoices/            20 photographed/scanned images (JPG) - receipts and GST tax invoices
```

Full findings from directly inspecting representative files from every category — page counts, whether
PDFs are native-text or scanned, table layouts, negative-number conventions, currency/unit notation,
OCR challenges, and the assumptions the extraction logic makes — are documented in
[`docs/dataset_analysis.md`](docs/dataset_analysis.md), generated by
[`scripts/analyze_dataset.py`](scripts/analyze_dataset.py) by literally running the application's own
OCR pipeline against every dataset file (nothing in that report is hand-typed).

## 6. OCR Approach

Layered, in `app/services/ocr_service.py`:

1. **Native text extraction** (`extract_text_from_pdf`) — PyMuPDF's `get_text()` per page.
2. **Quality check** (`is_text_quality_sufficient`) — a page is trusted as "native" only if it has
   enough alphanumeric content; every financial statement PDF in this dataset turned out to be a
   scanned image with zero native text, so this always falls through to OCR for them.
3. **OCR fallback** — the page is rendered to a bitmap at 300 DPI (`render_pdf_page`) and run through
   Tesseract (`run_ocr`). 300 DPI specifically matters: at a lower preview DPI (~144), OCR accuracy on
   small header text was visibly worse in testing.
4. **Table-aware row reconstruction** — this was the single highest-impact design decision. Tesseract's
   own page-segmentation groups text into blocks/lines by its own layout analysis, which for a wide
   table with far-apart columns (item name ... schedule ... value ... value) frequently puts the whole
   label column in one block and both value columns in separate blocks *later* in the output — i.e. it
   does **not** preserve "row" order for tables. `_reconstruct_rows` in `ocr_service.py` instead takes
   every OCR'd word's raw `(left, top, width, height)` box, clusters words into rows purely by vertical
   pixel position (tolerant to a fraction of the median word height), and sorts each row's words
   left-to-right. This reliably reconstructs `"Capital  1  1,539.34  765.22"` as one line, which a
   naive `pytesseract.image_to_string()` call does not. This is exposed as `PageText.row_text` /
   `table_lines` and is the primary text source financial-statement table parsing uses.
5. **Images (JPG/PNG)** go straight through the same OCR + row-reconstruction path.
6. **EXIF orientation correction** — phone photos frequently store rotation as EXIF metadata rather
   than rotating the actual pixels. Without correcting for this (`PIL.ImageOps.exif_transpose`), one
   dataset invoice photographed in portrait-with-EXIF-rotation-6 OCR'd as complete noise; after the fix
   it reads correctly. This is exactly the kind of bug real deployments hit constantly with
   phone-submitted documents.

Every extracted page keeps its `page_number`, so every field's `evidence`/`page_number` is traceable
back to where it was actually read.

## 7. Extraction Approach

`app/services/extraction_service.py` is purely rule-based (regex + heuristics) — **no LLM is required**
for the pipeline to work, which was a deliberate choice validated by running the whole dataset through
it (see §13). Each of the four document types has its own extractor function, but they share:

- **Number normalization** (`app/utils/number_utils.py`): thousands separators, parentheses/brackets as
  negative, currency symbols/codes, and explicit scale words (`crore`, `lakh`, `million`, `thousand`,
  and the `'000` notation) — only ever applied when the text explicitly says so, never guessed.
- **Financial-table row parsing**: reconstructed OCR rows are scanned; a header line with no monetary
  amount switches the "current section" (e.g. `ASSETS`, `Cash flows from operating activities:`); a
  line carrying `len(periods)` trailing monetary tokens becomes a structured line item
  `{section, name, values: {period: number}, page_number, evidence, is_total}`. A canonical
  alias table (e.g. "Interest Earned" → `interest_earned`, a `Total` row inside the `assets` section →
  `total_assets`) then builds the `totals` dict the financial validation engine reads.
- **Invoices**: labeled-field regexes for invoice number/date/vendor/customer/amounts, plus a
  table-region line-item parser that copes with two different real row shapes found in the dataset —
  `"<description> ... <price> <amount>"` (formal GST invoices) and `"<qty> <description> <price>
  <amount>"` (till receipts) — while rejecting numbers that are clearly part of a compound spec token
  (e.g. `"48X230ML"`) rather than a real price/amount column, since a genuine amount in this dataset's
  convention always carries a decimal point.

Every important field is emitted as `{"value", "confidence", "page_number", "evidence"}` (see
`app/utils/confidence_utils.make_field`). A field that cannot be reliably read is `null` — the code
never invents or infers a value that isn't backed by matched text.

## 8. Financial Validation

`app/services/financial_validation_service.py` is intentionally the *only* place arithmetic happens —
it never touches OCR or raw text, only the already-parsed numbers in `extracted_data`. Every check
returns:

```json
{
  "name": "total_income_check",
  "formula": "Interest Earned + Other Income == Total Income",
  "operands": {"interest_earned": 348615.15, "other_income": 146847.66},
  "calculated_value": 495462.81,
  "reported_value": 495462.81,
  "variance": 0.0,
  "status": "PASS",
  "period": "2026"
}
```

Implemented formulas:

- **Invoice**: `quantity × unit_price ≈ amount` per line, `sum(line items) ≈ subtotal`,
  `subtotal + tax − discount ≈ total`, `cash_paid − total ≈ change`.
- **Balance sheet**: `Total Capital & Liabilities ≈ Total Assets`, plus (when enough line items were
  read) `sum(asset components) ≈ total assets` and `sum(liability components) ≈ total liabilities`.
- **P&L**: all five case-study formulas (`Interest Earned + Other Income ≈ Total Income`,
  `Interest Expended + Operating Expenses + Provisions ≈ Total Expenditure`,
  `Total Income − Total Expenditure ≈ Net Profit before Minority Interest`,
  `Profit before MI − Minority Interest ≈ Net Profit attributable to Group`,
  `Current Profit + Brought Forward ≈ Total Available for Appropriation`), each period independently,
  plus generic `revenue − cost_of_sales ≈ gross_profit` for non-bank-format P&Ls.
- **Cash flow**: `Operating + Investing + Financing + FX ≈ Net Increase in Cash`,
  `Opening Cash + Net Increase ≈ Closing Cash`, each period independently.

**Tolerance** (`app/core/config.py`, overridable via env vars):

```
VALIDATION_ABSOLUTE_TOLERANCE=1.0    # absolute difference allowed
VALIDATION_RELATIVE_TOLERANCE=0.01   # 1% relative difference allowed
```

`app/utils/number_utils.numbers_match` passes if **either** tolerance is satisfied — this matters
because these are figures in crore/thousands with rounding in the source document itself, so a
same-document PASS should not require bit-exact floats.

If a required operand is missing, the check's status is `NOT_APPLICABLE` (never fabricated as PASS or
skipped silently) — every check the engine knows how to run is always present in the response, whether
or not it had the data to actually run it.

**`processing_status` logic** (`app/services/document_service.determine_processing_status`):
`FAILED` if either (a) extraction found no meaningful core data for that document type, or (b) any
financial check's status is `FAIL`. A document with only `NOT_APPLICABLE` checks (no arithmetic could
be verified because required fields weren't present) is **not** automatically failed for that reason
alone — only an actual mismatch does that.

## 9. Database

`app/models/document.py` — one `documents` table (id, document_name, document_type,
processing_status, file_type, page_count, is_supported, is_readable, overall_confidence,
file_validation JSON, extracted_data JSON, validation_result JSON, processing_metadata JSON,
created_at, updated_at). All access goes through `app/repositories/document_repository.py` — no raw
queries anywhere else. Re-processing the same `document_name` inserts a new row;
`GET /documents/{name}` returns the most recent one; `GET /documents` returns the latest version per
distinct name. `DATABASE_URL` defaults to a local SQLite file but is Postgres-ready — swapping to
`postgresql://user:pass@host:5432/db` requires no code changes (this is why `psycopg2-binary` is in
`requirements.txt`).

## 10. API Documentation

Base path: `/api/v1`. Interactive Swagger UI at **`/docs`**.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/documents/process` | Upload + process a document |
| `GET`  | `/api/v1/documents/{document_name}` | Latest result for a document |
| `GET`  | `/api/v1/documents` | List processed documents (filter/search) |
| `GET`  | `/api/v1/health` | Health check |

### 10.1 `POST /api/v1/documents/process`

`multipart/form-data`: `file` (PDF/JPG/PNG, ≤3 pages) + `document_type`
(`invoice` | `balance_sheet` | `profit_and_loss` | `cash_flow_statement`).

```bash
curl -X POST "http://localhost:8000/api/v1/documents/process" \
  -F "file=@dataset/Invoices/X51005361895.jpg" \
  -F "document_type=invoice"
```

### 10.2 Example response shape

```json
{
  "document_name": "Consolidated Balance Sheet 2026.pdf",
  "document_type": "balance_sheet",
  "processing_status": "PASS",
  "overall_confidence": 0.9446,
  "file_validation": {
    "file_type": "application/pdf",
    "is_supported": true,
    "is_readable": true,
    "page_count": 1,
    "status": "PASS",
    "issues": []
  },
  "extracted_data": {
    "company_name": {"value": "HDFC Bank Limited", "confidence": 0.95, "evidence": "HDFC  Bank  Limited"},
    "currency": {"value": "INR", "confidence": 0.95},
    "periods": ["2026", "2025"],
    "financial_line_items": [
      {"section": "assets", "name": "Cash and balances with Reserve Bank of India",
       "values": {"2026": 200707.11, "2025": 144390.25}, "page_number": 1, "is_total": false}
    ],
    "totals": {
      "total_assets": {"2026": 4908040.84, "2025": 4392417.42},
      "total_liabilities": {"2026": 4908040.84, "2025": 4392417.42}
    }
  },
  "validation": {
    "checks": [
      {"name": "total_assets_equals_total_liabilities_and_equity",
       "formula": "Total Capital & Liabilities == Total Assets",
       "calculated_value": 4908040.84, "reported_value": 4908040.84,
       "variance": 0.0, "status": "PASS", "period": "2026"}
    ],
    "overall_status": "PASS",
    "issues": []
  },
  "processing_metadata": {
    "ocr_used": true, "processed_at": "2026-09-10T15:34:54Z",
    "processing_time_ms": 4752, "ocr_provider": "tesseract",
    "extraction_provider": "rule_based"
  }
}
```

This is a real, actual response captured from the running pipeline against
`dataset/Balance Sheet/Consolidated Balance Sheet 2026.pdf` — see `sample_outputs/` for the full,
unedited files.

### 10.3 Other requests

```bash
curl "http://localhost:8000/api/v1/documents"
curl "http://localhost:8000/api/v1/documents?document_type=invoice&status=PASS"
curl "http://localhost:8000/api/v1/documents/Consolidated%20Balance%20Sheet%202026.pdf"
curl "http://localhost:8000/api/v1/health"
```

### 10.4 Error responses

Controlled, stack-trace-free JSON on every error path:

```json
{ "error": { "code": "UNSUPPORTED_FILE_TYPE", "message": "Only PDF / JPG / PNG documents are supported." } }
```

| Status | Code | When |
|---|---|---|
| 400 | `INVALID_DOCUMENT_TYPE` | `document_type` isn't one of the 4 accepted values |
| 400 | `EMPTY_FILE` | Uploaded file has 0 bytes |
| 404 | `DOCUMENT_NOT_FOUND` | `GET /documents/{name}` for a name never processed |
| 413 | `FILE_TOO_LARGE` | Exceeds `MAX_FILE_SIZE_MB` |
| 415 | `UNSUPPORTED_FILE_TYPE` | Bad extension, or extension/content-signature mismatch |
| 422 | `CORRUPTED_FILE` | File can't be decoded as a valid PDF/image |
| 422 | `PAGE_LIMIT_EXCEEDED` | PDF has more than `MAX_PAGES` pages |
| 422 | `VALIDATION_ERROR` | Malformed request (e.g. missing form field) |
| 500 | `INTERNAL_ERROR` / `PROCESSING_ERROR` | Unexpected failure - never leaks a stack trace |

## 11. Environment Variables

See [`.env.example`](.env.example) for the full list with defaults. Key ones:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | SQLite by default; point at PostgreSQL in production |
| `MAX_FILE_SIZE_MB`, `MAX_PAGES` | Upload limits |
| `OCR_LANGUAGE`, `TESSERACT_CMD`, `PDF_RENDER_DPI` | OCR tuning |
| `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL` | Optional LLM gap-fill; leave `LLM_PROVIDER=none` to disable |
| `VALIDATION_ABSOLUTE_TOLERANCE`, `VALIDATION_RELATIVE_TOLERANCE` | Financial check tolerance |
| `CORS_ORIGINS` | Comma-separated allowed origins, or `*` |

Never commit a real `.env` file — it's gitignored.

## 12. Local Setup

```bash
# 1. Extract the dataset into ./dataset (already done in this checkout)

# 2. Create and activate a virtualenv, install backend deps
python -m venv .venv
source .venv/Scripts/activate      # Windows Git Bash; use .venv\Scripts\activate.bat on cmd
pip install -r backend/requirements.txt

# 3. Install Tesseract OCR (not a pip package)
#    Windows: winget install --id UB-Mannheim.TesseractOCR -e
#    macOS:   brew install tesseract
#    Linux:   apt-get install tesseract-ocr

# 4. Copy environment config
cp .env.example backend/.env
```

### 12.1 Running the backend

```bash
cd backend
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
# Swagger UI:  http://127.0.0.1:8000/docs
# Health:      http://127.0.0.1:8000/api/v1/health
```

### 12.2 Running the frontend

```bash
cd frontend
pip install -r requirements.txt
export BACKEND_URL=http://localhost:8000     # or `set` on Windows cmd
python frontend_app.py
# Dashboard: http://localhost:5000
```

### 12.3 Running tests

```bash
cd backend
python -m pytest tests/ -v
```

48 tests across `test_validation.py` (file type/integrity/page-count rules),
`test_extraction.py` (number parsing, unit/currency detection, per-document-type extraction against
real dataset files, and financial-validation PASS/FAIL/NOT_APPLICABLE scenarios), and `test_api.py`
(end-to-end HTTP behavior including error responses) — all currently passing.

## 13. Dataset Test Results

Running the full dataset (50 files) through the live API end-to-end (not a mock) using
[`scripts/analyze_dataset.py`](scripts/analyze_dataset.py)'s companion batch-test run:

| Category | Files | PASS | FAILED |
|---|---|---|---|
| Balance Sheet | 10 | 6 | 4 |
| Cash Flow Statement | 10 | 7 | 3 |
| Profit & Loss | 10 | 5 | 5 |
| Invoices | 20 | 9 | 11 |
| **Total** | **50** | **27 (54%)** | **23 (46%)** |

Every `FAILED` result is a genuine "the arithmetic didn't reconcile" or "not enough was extracted from
a low-quality scan/photo", never a crash — the pipeline never throws an unhandled exception on any of
the 50 real files, and every response (PASS or FAILED) is a fully-formed, evidence-grounded JSON object
a reviewer can inspect. The financial-statement PDFs (bank-format, comparative 2-period tables) reach
55-70% full PASS despite being scanned images with no native text and, in several years, materially
different numeric conventions (see §14); the harder photographed invoices/receipts — with skew,
handwriting overlaid on printed text, and much more heterogeneous layouts — are naturally a lower-
accuracy OCR problem, and the system's honesty here (reporting `FAILED` rather than fabricating a
plausible-looking but wrong total) is the intended behavior per the case study's core principle.

## 14. Known Limitations

- **OCR quality varies by source scan.** One balance sheet year in the dataset has visibly lower scan
  quality than the others; several table rows OCR with digits dropped or merged, which the financial
  validation engine correctly reports as `FAIL` rather than silently accepting bad numbers. This is
  working as intended (don't fabricate), but it does mean extraction accuracy is bounded by input scan
  quality.
- **Comparative-year labels can be OCR-misread** (e.g. a "2023" header occasionally reads as "2003")
  even when the *values* under that column are read correctly and the arithmetic still validates
  correctly for that (mislabeled) period. The numbers are right; the year label attached to them can
  occasionally be wrong.
- **Photographed invoices are a harder problem than scanned statements**: skewed paper, handwriting/
  stamps overlapping printed text, and complex multi-column layouts (vendor block beside an invoice-
  metadata block at the same vertical position) all reduce OCR fidelity, and the two blocks can get
  merged by the row-reconstruction heuristic that otherwise works well for simple 2-column financial
  tables. Extraction on the cleanest invoices in the dataset is accurate; on the noisiest photographed
  ones several fields legitimately come back `null` rather than guessed.
- **Line-item column mapping is heuristic**, not a true table-structure model. It correctly separates
  qty/price/amount on 2-4 column layouts (including detecting whether quantity leads or trails the
  description) but is less reliable on complex GST invoices with many numeric columns (HSN code, two
  rate columns, discount %, amount) where numbers are also embedded inside product descriptions.
- **The optional LLM path was implemented against the documented Anthropic API but not exercised
  end-to-end in this environment** (no API key was available during development) — treat it as
  implemented-but-unverified. The deterministic pipeline is fully independent of it and was the one
  validated against the whole dataset.
- **Uploaded files are not retained** after processing (temp file is deleted once OCR/extraction
  finishes) — only the structured result is persisted. Combined with SQLite's file being on local disk,
  a production deployment on ephemeral storage should point `DATABASE_URL` at PostgreSQL (see §15).

## 15. Production Improvements

- Point `DATABASE_URL` at managed PostgreSQL; SQLite is fine for local dev but its file won't survive
  an ephemeral-filesystem redeploy on most PaaS platforms.
- Add authentication/authorization on the processing endpoints (currently open, matching the case
  study's scope).
- Add image preprocessing before OCR (deskew, contrast normalization, upscaling) — would likely improve
  the lower-quality scans and skewed phone photos noted in Limitations.
- Replace the bounding-box row-clustering heuristic with a proper table-structure model (e.g. a
  layout-aware model or a dedicated table-extraction library) for more reliable column alignment on
  complex multi-column invoices.
- Add object storage (S3-compatible) if uploaded originals need to be retained for audit/evidence
  beyond the extracted JSON.
- Background job queue for OCR (currently synchronous in the request) so large uploads don't block the
  request thread under load.

## 16. AI Tools Used

This solution was built with **Claude Code** (Anthropic), used for:
- code generation across the backend services, frontend, tests, and this documentation;
- debugging assistance — most notably diagnosing why Tesseract's default text output scrambled table
  row order (leading to the bounding-box row-reconstruction fix) and why one photographed invoice OCR'd
  as pure noise (EXIF orientation);
- architecture and design decisions (service/repository separation, the extraction alias-rule design,
  the tolerance-based financial comparison);
- iterative testing against the real dataset to find and fix extraction bugs (the whole 50-file dataset
  was run through the live API repeatedly during development to drive these fixes, not just used for a
  final demo).

The case study explicitly permits generative AI assistance; no part of this is claimed as written
without it.

## 17. Deployment

**Local Docker:**

```bash
docker-compose up --build
# backend:  http://localhost:8000  (Swagger at /docs)
# frontend: http://localhost:5000
```

**Cloud deployment (Render/Railway/Koyeb or equivalent):** this repo is deployment-ready but has not
been deployed to a live cloud URL from this environment (no hosting credentials/platform access were
available here). To deploy:

1. Push this repository to GitHub.
2. Create a **Web Service** from `backend/Dockerfile` (or Render's native Python runtime, installing
   `apt-get install tesseract-ocr` in the build step if not using the Dockerfile).
3. Set environment variables from `.env.example` — in particular point `DATABASE_URL` at the platform's
   managed PostgreSQL instance rather than SQLite.
4. Create a second **Web Service** from `frontend/Dockerfile`, with `BACKEND_URL` set to the deployed
   backend's public URL.
5. Set `CORS_ORIGINS` on the backend to the frontend's deployed URL.

| | URL |
|---|---|
| Frontend | _fill in after deploying_ |
| Backend API | _fill in after deploying_ |
| Swagger | `<backend URL>/docs` |
| Health | `<backend URL>/api/v1/health` |
| GitHub repo | _fill in_ |

## 18. Final Submission Checklist

- [x] Backend starts and serves `/docs`
- [x] Frontend starts and calls the backend API
- [x] Database persists and returns results (`GET` by name, `GET` list)
- [x] `/api/v1/health` works
- [x] Invoice / balance sheet / P&L / cash flow processing all work against real dataset files
- [x] Scanned-PDF and photographed-image OCR both work
- [x] Invalid file / oversized-page-count uploads are rejected with controlled errors
- [x] Financial validation produces real PASS/FAIL/NOT_APPLICABLE, never fabricated
- [x] 48 automated tests passing
- [x] Docker builds for both services
- [x] Architecture diagram, dataset analysis, and sample outputs generated from the real pipeline
- [ ] Deployed to a public URL (not done in this environment — see §17 for exact steps)
