const API_BASE = window.API_BASE_URL;
const DOCUMENT_NAME = window.DOCUMENT_NAME;

const LOW_CONFIDENCE_THRESHOLD = 0.6;

const TYPE_LABELS = {
  invoice: "Invoice",
  balance_sheet: "Balance Sheet",
  profit_and_loss: "Profit & Loss",
  cash_flow_statement: "Cash Flow Statement",
};

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function fmtNumber(n) {
  if (n === null || n === undefined) return "-";
  if (typeof n !== "number") return escapeHtml(n);
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function humanizeKey(key) {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

async function load() {
  try {
    const res = await fetch(`${API_BASE}/documents/${encodeURIComponent(DOCUMENT_NAME)}`);
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      const msg = data && data.error ? data.error.message : `HTTP ${res.status}`;
      throw new Error(msg);
    }
    const data = await res.json();
    render(data);
  } catch (e) {
    document.getElementById("load-error").style.display = "block";
    document.getElementById("load-error").innerHTML = `<h2>Could not load document</h2><p>${escapeHtml(e.message)}</p>`;
    document.getElementById("status-pill").textContent = "error";
  }
}

function render(data) {
  document.getElementById("doc-title").textContent = data.document_name;
  document.getElementById("doc-subtitle").textContent =
    `${TYPE_LABELS[data.document_type] || data.document_type} · processed ${data.processing_metadata?.processed_at || ""}`;
  const pill = document.getElementById("status-pill");
  pill.textContent = data.processing_status;
  pill.classList.add(data.processing_status === "PASS" ? "ok" : "bad");

  document.getElementById("result-content").style.display = "block";

  renderSummary(data);
  renderFileValidation(data.file_validation);
  renderExtractedData(data.extracted_data, data.document_type);
  renderValidation(data.validation);

  document.getElementById("json-viewer").textContent = JSON.stringify(data, null, 2);
  document.getElementById("copy-json-btn").addEventListener("click", () => {
    navigator.clipboard.writeText(JSON.stringify(data, null, 2));
  });
}

function summaryItem(label, value) {
  return `<div class="item"><div class="label">${escapeHtml(label)}</div><div class="value">${value}</div></div>`;
}

function renderSummary(data) {
  const conf = data.overall_confidence !== null && data.overall_confidence !== undefined
    ? `${Math.round(data.overall_confidence * 100)}%`
    : "n/a";
  const meta = data.processing_metadata || {};
  const grid = document.getElementById("summary-grid");
  grid.innerHTML = [
    summaryItem("Document Type", TYPE_LABELS[data.document_type] || escapeHtml(data.document_type)),
    summaryItem("Processing Status", `<span class="badge ${data.processing_status}">${data.processing_status}</span>`),
    summaryItem("Overall Confidence", conf),
    summaryItem("OCR Used", meta.ocr_used ? "Yes" : "No"),
    summaryItem("Processing Time", meta.processing_time_ms !== undefined ? `${meta.processing_time_ms} ms` : "-"),
    summaryItem("Processed At", escapeHtml(meta.processed_at || "-")),
  ].join("");
}

function renderFileValidation(fv) {
  if (!fv) return;
  const grid = document.getElementById("file-validation-grid");
  grid.innerHTML = [
    summaryItem("File Type", escapeHtml(fv.file_type)),
    summaryItem("Supported", fv.is_supported ? "Yes" : "No"),
    summaryItem("Readable", fv.is_readable ? "Yes" : "No"),
    summaryItem("Page Count", fv.page_count),
    summaryItem("Status", `<span class="badge ${fv.status}">${fv.status}</span>`),
  ].join("");
}

function isFieldShape(v) {
  return v && typeof v === "object" && !Array.isArray(v) && "value" in v;
}

function renderFieldBox(key, field) {
  const value = field.value;
  const isNull = value === null || value === undefined || value === "";
  const lowConf = typeof field.confidence === "number" && field.confidence < LOW_CONFIDENCE_THRESHOLD;
  const valueDisplay = isNull ? "Not found" : (typeof value === "number" ? fmtNumber(value) : escapeHtml(value));
  const confBadge = typeof field.confidence === "number"
    ? `<span class="tag">confidence ${Math.round(field.confidence * 100)}%</span>` : "";
  const pageBadge = field.page_number ? `<span class="tag">page ${field.page_number}</span>` : "";
  const evidence = field.evidence ? `<div class="fevidence">Evidence: &ldquo;${escapeHtml(field.evidence)}&rdquo;</div>` : "";
  return `
    <div class="field-box ${lowConf ? "low-conf" : ""}">
      <div class="fname">${escapeHtml(humanizeKey(key))}</div>
      <div class="fvalue ${isNull ? "null" : ""}">${valueDisplay}</div>
      <div>${confBadge}${pageBadge}</div>
      ${evidence}
    </div>`;
}

const SPECIAL_KEYS = new Set(["financial_line_items", "totals", "periods", "line_items", "unit_multiplier"]);

function renderExtractedData(extracted, documentType) {
  const fieldsContainer = document.getElementById("extracted-fields");
  const financialContainer = document.getElementById("financial-tables");
  const lineItemsContainer = document.getElementById("line-items-section");
  fieldsContainer.innerHTML = "";
  financialContainer.innerHTML = "";
  lineItemsContainer.innerHTML = "";

  if (!extracted) return;

  const boxes = [];
  for (const [key, value] of Object.entries(extracted)) {
    if (SPECIAL_KEYS.has(key)) continue;
    if (isFieldShape(value)) {
      boxes.push(renderFieldBox(key, value));
    } else if (value !== null && typeof value !== "object") {
      boxes.push(renderFieldBox(key, { value }));
    }
  }
  fieldsContainer.innerHTML = boxes.join("");

  if (Array.isArray(extracted.financial_line_items) && extracted.financial_line_items.length) {
    financialContainer.innerHTML = renderFinancialTable(extracted.financial_line_items, extracted.periods || [], extracted.totals || {});
  }

  if (Array.isArray(extracted.line_items) && extracted.line_items.length) {
    lineItemsContainer.innerHTML = renderLineItemsTable(extracted.line_items);
  }
}

function renderFinancialTable(lineItems, periods, totals) {
  const cols = periods.length ? periods : Object.keys(lineItems[0]?.values || {});
  const bySection = {};
  for (const item of lineItems) {
    const sec = item.section || "other";
    if (!bySection[sec]) bySection[sec] = [];
    bySection[sec].push(item);
  }

  let html = `<div class="section-title">Financial Line Items</div>`;

  const totalsEntries = Object.entries(totals).filter(([, v]) => v && Object.keys(v).length);
  if (totalsEntries.length) {
    html += `<div class="table-scroll"><table><thead><tr><th>Key Total</th>${cols.map((c) => `<th>${escapeHtml(c)}</th>`).join("")}</tr></thead><tbody>`;
    for (const [key, values] of totalsEntries) {
      html += `<tr><td><strong>${escapeHtml(humanizeKey(key))}</strong></td>${cols.map((c) => `<td>${fmtNumber(values[c])}</td>`).join("")}</tr>`;
    }
    html += `</tbody></table></div>`;
  }

  for (const [section, items] of Object.entries(bySection)) {
    html += `<div class="section-title" style="border-bottom:none;margin-bottom:4px;font-size:0.85rem;color:var(--text-muted);">${escapeHtml(humanizeKey(section))}</div>`;
    html += `<div class="table-scroll"><table><thead><tr><th>Line Item</th>${cols.map((c) => `<th>${escapeHtml(c)}</th>`).join("")}<th>Page</th></tr></thead><tbody>`;
    for (const item of items) {
      const rowStyle = item.is_total ? ' style="font-weight:700;background:#f8f9fc;"' : "";
      html += `<tr${rowStyle} title="${escapeHtml(item.evidence || "")}"><td>${escapeHtml(item.name)}</td>${cols
        .map((c) => `<td>${fmtNumber(item.values ? item.values[c] : null)}</td>`)
        .join("")}<td>${item.page_number ?? "-"}</td></tr>`;
    }
    html += `</tbody></table></div>`;
  }
  return html;
}

function renderLineItemsTable(items) {
  const cols = new Set();
  items.forEach((it) => Object.keys(it).forEach((k) => k !== "evidence" && cols.add(k)));
  const colList = Array.from(cols);
  let html = `<div class="section-title">Line Items</div><div class="table-scroll"><table><thead><tr>${colList
    .map((c) => `<th>${escapeHtml(humanizeKey(c))}</th>`)
    .join("")}</tr></thead><tbody>`;
  for (const item of items) {
    html += `<tr title="${escapeHtml(item.evidence || "")}">${colList
      .map((c) => {
        const v = item[c];
        if (v === undefined || v === null) return "<td>-</td>";
        if (Array.isArray(v)) return `<td>${v.map(fmtNumber).join(", ")}</td>`;
        return `<td>${typeof v === "number" ? fmtNumber(v) : escapeHtml(v)}</td>`;
      })
      .join("")}</tr>`;
  }
  html += `</tbody></table></div>`;
  return html;
}

function renderValidation(validation) {
  const summary = document.getElementById("validation-summary");
  const tbody = document.querySelector("#validation-table tbody");
  const issuesBox = document.getElementById("issues-box");
  if (!validation) {
    summary.innerHTML = "<p>No validation data.</p>";
    return;
  }
  summary.innerHTML = `<p>Overall status: <span class="badge ${validation.overall_status}">${validation.overall_status}</span> &nbsp;(${validation.checks.length} check(s) run)</p>`;

  if (!validation.checks.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty-state">No financial checks were applicable to this document.</td></tr>`;
  } else {
    tbody.innerHTML = validation.checks
      .map(
        (c) => `
      <tr>
        <td>${escapeHtml(humanizeKey(c.name))}</td>
        <td style="font-family:monospace;font-size:0.82rem;">${escapeHtml(c.formula)}</td>
        <td>${escapeHtml(c.period || "-")}</td>
        <td>${fmtNumber(c.calculated_value)}</td>
        <td>${fmtNumber(c.reported_value)}</td>
        <td>${fmtNumber(c.variance)}</td>
        <td><span class="badge ${c.status}">${c.status}</span></td>
      </tr>`
      )
      .join("");
  }

  if (validation.issues && validation.issues.length) {
    issuesBox.innerHTML = `<div class="section-title">Issues</div><ul class="issue-list">${validation.issues
      .map((i) => `<li>${escapeHtml(i)}</li>`)
      .join("")}</ul>`;
  } else {
    issuesBox.innerHTML = "";
  }
}

load();
