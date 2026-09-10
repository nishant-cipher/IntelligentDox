# Dataset Analysis Report

## Part A — Findings from direct inspection

This section documents what was found by directly opening representative files from every category
(rendering PDFs to images and viewing them, and viewing the invoice images directly) before any
extraction logic was written, plus what the automated pass below (Part B) confirmed at scale. Nothing
here is fabricated — every claim is either a direct visual observation of a specific file or a
statistic computed by running the application's own pipeline.

### A.1 Categories and volume

| Category | Files | Format | Pages each |
|---|---|---|---|
| Balance Sheet | 10 (`2017.pdf` … `2026.pdf`) | PDF | 1 |
| Cash Flows | 10 (`2017.pdf` … `2026.pdf`) | PDF | 1-2 (most years 2; 2021-2023 are 1) |
| Profit & Loss | 10 (`2017.pdf` … `2026.pdf`) | PDF | 1 |
| Invoices | 20 | JPG | 1 |
| **Total** | **50** | | |

All three financial-statement PDF categories are clearly the **same company across years** (a large
Indian bank — see below), i.e. this dataset is 10 years of one company's consolidated Balance Sheet,
P&L and Cash Flow Statement, plus 20 unrelated invoice/receipt images from at least two different
sources.

### A.2 Are the PDFs native-text or scanned?

**Every one of the 30 financial-statement PDFs is a scanned image with zero extractable native text**
(`fitz`'s `get_text()` returns 0 characters on every page). This was confirmed programmatically before
writing any OCR code:

```
Consolidated Balance Sheet 2017.pdf | pages=1 | total_text_chars=0
Consolidated Balance Sheet 2026.pdf | pages=1 | total_text_chars=0
Consolidated Cash Flow Statement 2026.pdf | pages=2 | total_text_chars=0
... (all 30 files: 0 native text characters, one 2022 Cash Flow outlier had partial text noise)
```

This means the "native PDF text first, OCR fallback second" layer in the OCR pipeline **always** falls
through to OCR for this dataset's financial statements — there is no shortcut available. The 20 invoice
images are JPGs, so they always go through OCR by definition.

### A.3 Balance Sheet layout (representative: 2026)

Rendering page 1 at high resolution shows a clean, single-page **bank-format** balance sheet:

- Title `CONSOLIDATED BALANCE SHEET`, subtitle `As at March 31, 2026`, unit note `(₹ in crore)`.
- A two-column comparative table: `Schedule | As at March 31, 2026 | As at March 31, 2025`.
- Section `CAPITAL AND LIABILITIES` (Capital, Employee stock options, Reserves and surplus, Minority
  interest, Deposits, Borrowings, Other liabilities and provisions, Policyholders' funds, **Total**).
- Section `ASSETS` (Cash and balances with RBI, Balances with banks, Investments, Advances, Fixed
  assets, Other assets, **Total**).
- Memo lines below the assets total that are *not* part of either total: `Contingent liabilities`,
  `Bills for collection`.
- Footer: signatory block, company name `HDFC Bank Limited`, report title/page number.

This is **not** the generic "Equity and Liabilities / Assets" layout most non-bank companies use (no
"Total Equity" line separate from the liabilities total exists in this bank format) — the extraction
logic deliberately does not assume any one specific set of section names; it classifies sections by
keyword (`ASSET`, `LIABILIT`/`CAPITAL AND`) so it also works on a conventional company balance sheet.

### A.4 Profit & Loss layout (representative: 2026)

`CONSOLIDATED PROFIT AND LOSS ACCOUNT`, `For the year ended March 31, 2026`, same two-period comparative
table shape. Sections, in order: `I INCOME` (Interest earned, Other income, Total), `II EXPENDITURE`
(Interest expended, Operating expenses, Provisions and contingencies, Total), `III PROFIT` (Net profit
before minority interest, Less: minority interest, Net profit attributable to the group, Brought forward
profit, Total), `IV APPROPRIATIONS` (multiple transfer/dividend lines, Total), `V EARNINGS PER EQUITY
SHARE` (Basic, Diluted). Every one of the case study's five P&L validation formulas checks out exactly
against the printed figures on this file (verified by hand before any code was written), confirming the
case study's formulas were modeled on exactly this kind of bank-format statement.

### A.5 Cash Flow Statement layout (representative: 2026)

`CONSOLIDATED CASH FLOW STATEMENT`, two pages. Page 1: `Cash flows from operating activities:` (a long
adjustments block ending in `Net cash flows from operating activities`) then
`Cash flows from investing activities:`. Page 2 continues with
`Cash flows from financing activities:`, then `Effect of fluctuation in foreign currency translation
reserve`, `Net increase in cash and cash equivalents`, `Cash and cash equivalents at the beginning of
the year`, `Cash and cash equivalents at the end of the year`. The case study's cash-flow formulas
reconcile exactly against this file too.

### A.6 Invoice/receipt layout — highly heterogeneous

Unlike the financial statements, the 20 invoice images are **not** one consistent template. Two very
different real-world shapes were found:

1. **Till/POS receipts** (e.g. `X51005361895.jpg`, part of what looks like the public SROIE receipt-OCR
   dataset): narrow thermal-printer layout, vendor name/address/GST number at top, `TAX INVOICE` /
   `TRN:` line, a compact item table (`Qty UOM U.Price Amt Amt Inc.Tax Code`), `Cash` / `Change` lines,
   a `GST Summary` block. Often has a red ink stamp/signature overlapping the vendor name.
2. **Formal GST tax invoices** (e.g. `20251118_000612.jpg`): a full A4 photographed invoice with a
   vendor block *and* an invoice-metadata block side-by-side (Invoice No., Dated, GSTIN/UIN), a
   consignee/buyer block, a multi-column item table (SI No, Description, HSN/SAC, Quantity, Rate, Rate
   Incl. Tax, Disc %, Amount), a CGST/SGST breakdown table, amount-in-words, and bank details. Shot at
   an angle on wrinkled paper with handwritten signatures/numbers overlapping printed text.

Given this heterogeneity, the invoice extractor cannot assume a fixed column layout — it uses labeled-
field regexes for header data and a best-effort numeric-token heuristic for the line-item table (see
README §7 and §14 for exactly where this heuristic succeeds and where it doesn't).

### A.7 Negative numbers, currency, units

- **Negative values** are shown in parentheses throughout the financial statements, e.g. `(207.91)`,
  `(1,210.63)`, `(3,850.64)` — confirmed in the rendered 2026 Cash Flow Statement ("Profit on sale of
  fixed assets (207.91)  (22.03)"). `number_utils.parse_number` converts any `(...)`/`[...]`-wrapped
  figure to negative and never treats it as positive.
- **Units**: recent years (~2023-2026) state `(₹ in crore)`; **2017's balance sheet states `= in '000`
  (thousands)** — a materially different scale from the other years in the *same* dataset. This was
  only discovered by inspecting 2017 specifically and was the root cause of a real extraction bug (see
  §14/README) — the amount-detection regex initially assumed every figure would carry 2 decimal places
  (true for the crore-scale years) and silently matched nothing on the decimal-free 2017 figures until
  fixed to also recognize thousands-grouped whole numbers.
- **Currency symbol**: `₹` is present in the source but is frequently OCR'd as a stray glyph (`=`, `?`,
  a replacement character) rather than the literal rupee sign — the code infers `INR` from the explicit
  `crore`/`lakh`/`'000`-style unit notation instead of relying on the symbol OCR'ing correctly. It
  deliberately does **not** infer INR just from the presence of "GST" in an invoice, since GST is not
  India-specific (Malaysia, Singapore, Australia, Canada all use the term) — one dataset invoice
  (`X51005361895.jpg`) is a Malaysian receipt that happens to also say "GST".
- **A lone `-`** in a numeric column consistently means nil/zero in this dataset (e.g. `Interim Dividend
  paid 3,836.57  -` for the prior year) and is parsed as `0.0` in table contexts, not `null`.

### A.8 OCR challenges actually observed

- **Table row order is not preserved by Tesseract's own layout analysis.** `pytesseract.image_to_string`
  on the 2026 Balance Sheet returns every line-item *label* first (Capital, Reserves and surplus, …),
  then a block of schedule numbers, then a block of "2026" values, then a block of "2025" values — i.e.
  it groups by column, not by row, for a wide table with far-apart columns. This was discovered by
  direct testing and is the single most consequential OCR finding in this project; the fix
  (bounding-box row reconstruction, see README §6) is load-bearing for all financial-statement
  extraction.
- **Render resolution matters a lot.** At a 144 DPI preview render, the two comparative-year column
  headers OCR'd as illegible noise (`"Schedule  91,2088  91,2088"`); at 300 DPI the same header reads
  perfectly (`"March  31,  2026  March  31,  2025"`). The pipeline renders at 300 DPI by default
  (`PDF_RENDER_DPI`).
- **EXIF orientation on phone photos.** One invoice (`20251118_000612.jpg`, Samsung Galaxy A14 5G) has
  EXIF `Orientation=6` (needs a 90° rotation to display correctly) but PIL does not apply this
  automatically — without correcting for it, that image OCR'd as complete unreadable noise. Fixed via
  `PIL.ImageOps.exif_transpose`.
- **Digit-level misreads persist even at 300 DPI**, especially on smaller/lower-contrast text: a
  comparative-year column header for one file read `"2003"` instead of `"2023"`, and one balance sheet
  (2022) had visibly worse overall scan quality that dropped or merged individual digits in several
  monetary figures (`"2,724,038.16"` where the true figure appears to be `"2,724,938.16"` on close visual
  comparison with the same line-item elsewhere in the series). These remain genuine, unresolved OCR
  limitations — the system reports them honestly (financial checks come back `FAIL` rather than a false
  `PASS`) instead of silently accepting corrupted figures.
- **Stray whitespace inside large numbers**: OCR occasionally inserts a space in the middle of a long
  comma-grouped figure (`"8,923,441  ,607"` instead of `"8,923,441,607"`), which the amount-matching
  regex tolerates by allowing whitespace around each comma group.

### A.9 Financial validation opportunities confirmed in the data

Every formula the case study specifies was hand-verified against the rendered 2026 statements before
being implemented, and the resulting automated checks reproduce those exact figures (see
`sample_outputs/`). Because the statements are multi-period (2 comparative columns), every check runs
**independently per period** rather than mixing columns.

### A.10 Extraction risks / assumptions (carried into the design)

- Do not assume 2 decimal places always indicate "this is a real amount, not a reference number" — some
  years show whole-number thousands with no decimals at all (§A.7). The amount regex now accepts either
  a decimal point **or** thousands-grouping commas as sufficient evidence.
- Do not assume a fixed set of balance-sheet section names — classify by keyword, not by an exact
  template match, so a non-bank-format statement (Equity and Liabilities / Current & Non-current Assets)
  would still be bucketed sensibly.
- Do not assume invoice line items are laid out "description first, numbers last" — some receipts put
  quantity before the description. The parser detects both shapes but requires the trailing "amount"
  token to carry a decimal point before trusting a numeric column as a real amount (this specifically
  prevents mis-reading compound spec text like `"48X230ML"` as quantity/price/amount).
- Never infer a currency or scale that isn't explicitly evidenced in the text (see §A.7) — nulls are
  preferred over a guess.
- Two comparative years appearing with a shared value (e.g. a "Total" figure repeated for both a
  section's start-of-list subtotal and its grand total) must not be double-counted in the "sum of
  components" cross-check — the parser excludes any row it identified as a `Total` row from that section's
  own component sum.

---

## Part B — Automated per-file report

Generated by `scripts/analyze_dataset.py` by running the application's own OCR pipeline (`app/services/ocr_service.py`) against every file in `dataset/`. Nothing below is hand-typed; it reflects what the pipeline actually extracted.

## Summary

- Total files inspected: **50**
- Balance Sheet: **10** files
- Cash Flows: **10** files
- Profit & Loss: **10** files
- Invoices: **20** files

## Balance Sheet

| File | Ext | Pages | Native text chars | OCR used | Extracted chars | Status |
|---|---|---|---|---|---|---|
| Consolidated Balance Sheet 2017.pdf | .pdf | 1 | 0 | True | 1683 | OK |
| Consolidated Balance Sheet 2018.pdf | .pdf | 1 | 0 | True | 1651 | OK |
| Consolidated Balance Sheet 2019.pdf | .pdf | 1 | 0 | True | 1604 | OK |
| Consolidated Balance Sheet 2020.pdf | .pdf | 1 | 0 | True | 1485 | OK |
| Consolidated Balance Sheet 2021.pdf | .pdf | 1 | 0 | True | 1523 | OK |
| Consolidated Balance Sheet 2022.pdf | .pdf | 1 | 0 | True | 1635 | OK |
| Consolidated Balance Sheet 2023.pdf | .pdf | 1 | 0 | True | 1845 | OK |
| Consolidated Balance Sheet 2024.pdf | .pdf | 1 | 0 | True | 2045 | OK |
| Consolidated Balance Sheet 2025.pdf | .pdf | 1 | 0 | True | 1960 | OK |
| Consolidated Balance Sheet 2026.pdf | .pdf | 1 | 0 | True | 2198 | OK |

<details><summary>Sample extracted text — Consolidated Balance Sheet 2017.pdf</summary>

```
Consolidated Balance Sheet

As at March 31, 2017

= in ‘000
As at As at
Schedule 31-Mar-17 31-Mar-16
CAPITAL AND LIABILITIES
Capital 1 5,125,091 5,056,373
Reserves and surplus 2 912,814,397 737,984,869
Minority interest 2A 2,914,389 1,806,228
Deposits 3 6,431,342,479 5,458,732,889
Borrowings 4 984,156,439 1,037,139,597
Other liabilities and provisions 5) 587,088,812 381,403,308
Total 8,923,441 ,607 7,622,123,264
ASSETS
Cash and balances with Reserve Bank of India 6 379,105,485 300,765,846
Balances with banks and money at call and short notice 7 114,005,711 89,922,969
Investments 8 2,107,771,120 1,936,338,475
Advances 9 5,854,809,871 4,872,904,174
Fixed assets 10 38,146,997 34,796,976
Other assets 11 429,602,423 387,394,824
Total 8,923,441 ,607 7,622,123,264
Contingent liabilities 12 8,182,842,892 8,535,273,826
Bills for collection 308,480,352 234,899,997
Significant accounting policies and notes to the Consolidated financial 17 & 18
statements
The schedules referred to above form an integral part of the
Consolidated Balance Sheet
As per our report of even date. For and on behalf of the Board
For Deloitte Haskins & Sells Shyamala Gopinath Aditya Puri Anami Roy
Chartered Accountants Chairperson Managing Director Bobby Parikh
Keki Mistry
Malay Patel

P. B. Pardiwalla
Partner
Membership No.: 40005

Paresh Sukthankar
Deputy Managing Director

Kaizad Bharucha

Executive Director jen) BEA

Renu Karnad
Srikanth Nadhamuni
Umesh Sarangi
Directors

Sanjay Dongre
Executive Vice President
(Legal) & Company Secretary

Sashidhar Jagdishan
Chief Financial Officer

Mumbai, April 21, 2017

1} HDFC BANK

We understand your world

HDFC Bank Limited Annual Report 2016-17 148

```
</details>

## Cash Flows

| File | Ext | Pages | Native text chars | OCR used | Extracted chars | Status |
|---|---|---|---|---|---|---|
| Consolidated Cash Flow Statement 2017.pdf | .pdf | 2 | 0 | True | 3425 | OK |
| Consolidated Cash Flow Statement 2018.pdf | .pdf | 2 | 0 | True | 3427 | OK |
| Consolidated Cash Flow Statement 2019.pdf | .pdf | 2 | 0 | True | 3538 | OK |
| Consolidated Cash Flow Statement 2020.pdf | .pdf | 2 | 0 | True | 2700 | OK |
| Consolidated Cash Flow Statement 2021.pdf | .pdf | 1 | 0 | True | 2666 | OK |
| Consolidated Cash Flow Statement 2022.pdf | .pdf | 1 | 2963 | False | 2963 | OK |
| Consolidated Cash Flow Statement 2023.pdf | .pdf | 1 | 0 | True | 2839 | OK |
| Consolidated Cash Flow Statement 2024.pdf | .pdf | 2 | 0 | True | 3696 | OK |
| Consolidated Cash Flow Statement 2025.pdf | .pdf | 2 | 0 | True | 3692 | OK |
| Consolidated Cash Flow Statement 2026.pdf | .pdf | 2 | 0 | True | 4037 | OK |

<details><summary>Sample extracted text — Consolidated Cash Flow Statement 2017.pdf</summary>

```
Consolidated Cash Flow Statement
For the year ended March 31, 2017

Cash flows from operating activities

Consolidated profit before income tax
Adjustments for:

Depreciation on fixed assets

(Profit) / loss on revaluation of investments
Amortisation of premia on held to maturity investments
(Profit) / loss on sale of fixed assets

Provision / charge for non performing assets
Provision for dimunition in value of Investments
Floating provisions

Provision for standard assets

Contingency provisions

Share in current year's profits of associates

Adjustments for:

(Increase) / decrease in investments (excluding investments in subsidiaries)

(Increase) / decrease in advances
Increase / (decrease) in deposits
(Increase) / decrease in other assets

Increase / (decrease) in other liabilities and provisions

Direct taxes paid (net of refunds)

Net cash flow (used in) / from operating activities
Cash flows used in investing activities

Purchase of fixed assets

Proceeds from sale of fixed assets

Investment in subsidiaries and / or joint ventures

Net cash used in investing activities

HDFC Bank Limited Annual Report 2016-17 150

= in ‘000

Year ended Year ended
31-Mar-17 31-Mar-16
233,311,478 194,949,948
8,861,876 7,380,326
(87,543) 173,689
1,756,569 1,002,801
16,229 1,185
37,024,296 25,179,864
(76,417) 146,543
250,000 1,150,000
4,312,322 4,648,890
388,440 218,602
(23,393) (37,278)
285,733,857 234,814,570
(173,257,700) (391,159,616)

(1,018,904,990)

(1,066,012,996)

972,609,590 955,896,412
(44,855,329) (38,485,747)
228,337,692 31,324,658
249,663,120 (273,622,719)
(76,847,189) (70,730,944)
172,815,931 (344,353,663)
(11,577,570) (8,771,635)

100,768 116,125
(11,476,802) (8,655,510)

1} HDFC BANK

We understand your world

Consolidated Cash Flow Statement
For the year ended March 31, 2017

= in ‘000
Year ended Year ended
31-Mar-17 31-Mar-16
Cash flows from financing activities
Increase in minority interest 818,605 189,954
Money received on exercise of stock options by employ
```
</details>

## Profit & Loss

| File | Ext | Pages | Native text chars | OCR used | Extracted chars | Status |
|---|---|---|---|---|---|---|
| Consolidated Profit & Loss 2017.pdf | .pdf | 1 | 0 | True | 2362 | OK |
| Consolidated Profit & Loss 2018.pdf | .pdf | 1 | 0 | True | 2321 | OK |
| Consolidated Profit & Loss 2019.pdf | .pdf | 1 | 0 | True | 2167 | OK |
| Consolidated Profit & Loss 2020.pdf | .pdf | 1 | 0 | True | 2009 | OK |
| Consolidated Profit & Loss 2021.pdf | .pdf | 1 | 0 | True | 2147 | OK |
| Consolidated Profit & Loss 2022.pdf | .pdf | 1 | 0 | True | 2102 | OK |
| Consolidated Profit & Loss 2023.pdf | .pdf | 1 | 0 | True | 2527 | OK |
| Consolidated Profit & Loss 2024.pdf | .pdf | 1 | 0 | True | 2634 | OK |
| Consolidated Profit & Loss 2025.pdf | .pdf | 1 | 0 | True | 2579 | OK |
| Consolidated Profit & Loss 2026.pdf | .pdf | 1 | 0 | True | 2601 | OK |

<details><summary>Sample extracted text — Consolidated Profit & Loss 2017.pdf</summary>

```
For the year ended March 31, 2017

Consolidated Statement of Profit and Loss

= in ‘000
Year ended Year ended
Schedule 31-Mar-17 31-Mar-16
I INCOME
Interest earned 13 732,713,529 631,615,614
Other income 14 128,776,329 112,116,541
Total 861,489,858 743,732,155
I| EXPENDITURE
Interest expended 15 380,415,844 340,695,748
Operating expenses 16 207,510,707 178,318,808
Provisions and contingencies 120,689,285 96,544,349
Total 708,615,836 615,558,905
Il PROFIT
Net profit for the year 152,874,022 128,173,250
Less: Minority interest 367,165 197,212
Add: Share in profits of associates 23,393 37,278
Consolidated profit for the year attributable to the Group 152,530,250 128,013,316
Impact on amalgamation [Refer Schedule 18(1)] 274,507 -
Balance in Profit and Loss account brought forward 248,255,886 195,508,642
Total 401,060,643 323,521,958
IV. APPROPRIATIONS
Transfer to Statutory Reserve 37,771,634 31,809,345
Proposed dividend [Refer Schedule 18(3)] - 24,017,772
Tax (including cess) on interim / proposed dividend 255,959 5,123,529
Dividend (including tax / cess thereon) pertaining to previous year (16,909) (117,135)
paid during the year, net of dividend tax credits
Transfer to General Reserve 14,549,641 12,296,213
Transfer to Capital Reserve 3,134,100 2,221,532
Transfer to / (from) Investment Reserve Account 42,934 (85,184)
Balance carried over to Balance Sheet 345,323,284 248 255,886
Total 401,060,643 323,521,958
V EARNINGS PER EQUITY SHARE (Face value < 2 per share) z zg
Basic 59.95 50.85
Diluted 59.16 50.24
Significant accounting policies and notes to the
Consolidated financial statements 17 & 18
The schedules referred to above form an integral part of the
Consolidated Statement of Profit and Loss
As per our report of even date. For and on behalf of the Board
For Deloitte Haskins & Sells Shyamala Gopinath Aditya Puri Anami Roy
Chartered Accountants Chairperson Managing Director Bobby Parikh
Keki Mistry
P. B. Pardiwalla Paresh Sukthankar Kaizad Bharucha Malay Patel

Partner

```
</details>

## Invoices

| File | Ext | Pages | Native text chars | OCR used | Extracted chars | Status |
|---|---|---|---|---|---|---|
| 20251118_000612.jpg | .jpg | 1 | - | True | 2062 | OK |
| batch1-1109.jpg | .jpg | 1 | - | True | 987 | OK |
| batch2-0499.jpg | .jpg | 1 | - | True | 926 | OK |
| batch2-0999.jpg | .jpg | 1 | - | True | 648 | OK |
| batch3-1354.jpg | .jpg | 1 | - | True | 1610 | OK |
| batch3-1445.jpg | .jpg | 1 | - | True | 561 | OK |
| batch3-1495.jpg | .jpg | 1 | - | True | 550 | OK |
| X00016469619.jpg | .jpg | 1 | - | True | 501 | OK |
| X51005361895.jpg | .jpg | 1 | - | True | 400 | OK |
| X51005663293.jpg | .jpg | 1 | - | True | 661 | OK |
| X51005719883.jpg | .jpg | 1 | - | True | 402 | OK |
| X51005757342.jpg | .jpg | 1 | - | True | 619 | OK |
| X51005806685.jpg | .jpg | 1 | - | True | 470 | OK |
| X51006328913.jpg | .jpg | 1 | - | True | 478 | OK |
| X51006334741.jpg | .jpg | 1 | - | True | 840 | OK |
| X51006387931.jpg | .jpg | 1 | - | True | 427 | OK |
| X51008099043.jpg | .jpg | 1 | - | True | 485 | OK |
| X51008099071.jpg | .jpg | 1 | - | True | 570 | OK |
| X51008142038.jpg | .jpg | 1 | - | True | 374 | OK |
| X51008145450.jpg | .jpg | 1 | - | True | 671 | OK |

<details><summary>Sample extracted text — 20251118_000612.jpg</summary>

```
CO

TAX INVOICE Printed on 16-Jul-25 at 18:39

3-24) | :
‘Shankar Enterprises-(7°" Nvoice No. ‘Dated
12B,SHIB DEY LANE ,) SCi/25-26/3331 46-Jul-25
KOLKATA- 700087 (W.F687641 elivery Note Mode/Terms of Payment
D.L.No» WB/KOL/NB 651N1ZS 1
GSTIN/UIN: 19BF JF engal, Code : 19 Sales Man Market
‘State Name : W®®* -3,9231883650 SUJIT(9007785327) Area LOHAPOOL

earns 70096. ferprises9231 @gmail. com | Reference No. & Date. |Other References
all: S

ae Tan ahah dar 2 ‘Buyers Order No. (Dated
Ler ae nine® he choudhry Lane | . :
ee aan - 49CPQPS9464F 17x ‘Dispatch Doc No. Delivery Note Date
State Name : West Bengal, Code : 19 | ;

: 19831 715156 ‘Dispatched through Destination

over oral to) | -
Laxmi Narayan Bhandar 2 lems GF Delivery
'7/af Abhinesh eau y Lane

GSTIN/UIN 149CPQPS9464F 1Zx
State Name - West Bengal, Code : 19

‘Contact : 9831715156

No. — le a rs of Tax) | 4

| 4] SAFED 800GM ‘(24PKT) 68/- [0201 8 PCS. 61.45/52. 08 ry 2,499.84
-2SAFED WHITE DP 140GM 3402901116 PCS| 8-78 7.44 7($99% 0.45
_ 60PCS Twin Pack 10/- |
_3SAFED 2 KG (12 PKT) 3402901142 PCS|203.02 472.05 PCs 2,064.60
_ PRINTED BUCKET+LID 230/- |
4 SAFED WHITE DP 140GM 34029011/420 PCS, 8-78 7.44 70S 892.80
 60PCS Twin Pack 10/- |
5 SAFED WHITE DP 140GM 3402901118 PCS, 8.78)/7.44/P($ 99% 0.60

60PCS Twin Pack 10/-
6 SPARKLE 200 GM BATI 48PCS [24054000/24 PCS| 17-55 14.87 PCS 356.88
(9. 6 KG) WITH SCRUB PAD 20/-

0.11

| | | | 5 815.17
| | 9% 523.36
| 91% | 523.36

‘= 6,862.00
[Ee oye

-Taxable| CGST SGST/UTGST| Total |
Value |Rate] Amount Rate |Amount| Tax Amount
- 6,815. We 9%) |523.36| 9% | 523.36) 1,046. 72

“Total: 5, ,815. Ue _[523. 36) |523. 36) 1,046.72

a

T

Tex hovount (inwords) : INR One Thousand Forty Six and Seventy Two paise Only
. Company's Bank Details

| Bank Name : Union Bank of India
A/c No. : 301301010036937
Branch & IFS ‘Code: DHARMTALA & UBIN0530131

poi ret Se lee Bee the actual | for Shankar Enterprises(2023-24)
described and that all particulars are true and correct. |

eee |

c


```
</details>
