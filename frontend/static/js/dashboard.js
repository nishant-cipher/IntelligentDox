const API_BASE = window.API_BASE_URL;

const healthPill = document.getElementById("health-pill");
const uploadStatus = document.getElementById("upload-status");
const processBtn = document.getElementById("process-btn");
const fileInput = document.getElementById("file-input");
const docTypeSelect = document.getElementById("document-type");
const tbody = document.getElementById("documents-tbody");
const searchInput = document.getElementById("search-input");
const filterType = document.getElementById("filter-type");
const filterStatus = document.getElementById("filter-status");

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function formatDate(iso) {
  if (!iso) return "-";
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch (e) {
    return iso;
  }
}

async function checkHealth() {
  try {
    const res = await fetch(`${API_BASE}/health`);
    const data = await res.json();
    if (data.status === "healthy") {
      healthPill.textContent = "API healthy";
      healthPill.classList.add("ok");
    } else {
      healthPill.textContent = "API degraded";
      healthPill.classList.add("bad");
    }
  } catch (e) {
    healthPill.textContent = "API unreachable";
    healthPill.classList.add("bad");
  }
}

async function loadDocuments() {
  tbody.innerHTML = `<tr><td colspan="6" class="empty-state">Loading&hellip;</td></tr>`;
  const params = new URLSearchParams();
  if (filterType.value) params.set("document_type", filterType.value);
  if (filterStatus.value) params.set("status", filterStatus.value);
  if (searchInput.value.trim()) params.set("search", searchInput.value.trim());

  try {
    const res = await fetch(`${API_BASE}/documents?${params.toString()}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderStats(data.documents);
    renderTable(data.documents);
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty-state">Failed to load documents: ${escapeHtml(e.message)}</td></tr>`;
  }
}

function renderStats(docs) {
  document.getElementById("stat-total").textContent = docs.length;
  document.getElementById("stat-pass").textContent = docs.filter((d) => d.processing_status === "PASS").length;
  document.getElementById("stat-fail").textContent = docs.filter((d) => d.processing_status === "FAILED").length;
  const latest = docs.reduce((acc, d) => (!acc || d.created_at > acc ? d.created_at : acc), null);
  document.getElementById("stat-latest").textContent = latest ? formatDate(latest) : "-";
}

const TYPE_LABELS = {
  invoice: "Invoice",
  balance_sheet: "Balance Sheet",
  profit_and_loss: "Profit & Loss",
  cash_flow_statement: "Cash Flow Statement",
};

function renderTable(docs) {
  if (!docs.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty-state">No documents processed yet. Upload one above to get started.</td></tr>`;
    return;
  }
  tbody.innerHTML = docs
    .map((d) => {
      const conf = d.overall_confidence !== null && d.overall_confidence !== undefined
        ? `${Math.round(d.overall_confidence * 100)}%`
        : "-";
      return `
      <tr>
        <td>${escapeHtml(d.document_name)}</td>
        <td>${TYPE_LABELS[d.document_type] || escapeHtml(d.document_type)}</td>
        <td><span class="badge ${d.processing_status}">${d.processing_status}</span></td>
        <td>${conf}</td>
        <td>${formatDate(d.created_at)}</td>
        <td><a href="/document/${encodeURIComponent(d.document_name)}">View →</a></td>
      </tr>`;
    })
    .join("");
}

async function processDocument() {
  const file = fileInput.files[0];
  if (!file) {
    uploadStatus.textContent = "Please choose a file first.";
    uploadStatus.className = "status-line error";
    return;
  }
  const documentType = docTypeSelect.value;

  processBtn.disabled = true;
  processBtn.innerHTML = `<span class="spinner"></span>Processing&hellip;`;
  uploadStatus.textContent = "Uploading and processing - OCR can take a few seconds for scanned documents.";
  uploadStatus.className = "status-line";

  const formData = new FormData();
  formData.append("file", file);
  formData.append("document_type", documentType);

  try {
    const res = await fetch(`${API_BASE}/documents/process`, { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) {
      const message = data && data.error ? data.error.message : `HTTP ${res.status}`;
      throw new Error(message);
    }
    uploadStatus.textContent = `Done: ${data.document_name} → ${data.processing_status}`;
    uploadStatus.className = data.processing_status === "PASS" ? "status-line success" : "status-line error";
    fileInput.value = "";
    loadDocuments();
  } catch (e) {
    uploadStatus.textContent = `Error: ${e.message}`;
    uploadStatus.className = "status-line error";
  } finally {
    processBtn.disabled = false;
    processBtn.textContent = "Process Document";
  }
}

processBtn.addEventListener("click", processDocument);
document.getElementById("refresh-btn").addEventListener("click", loadDocuments);
searchInput.addEventListener("input", debounce(loadDocuments, 350));
filterType.addEventListener("change", loadDocuments);
filterStatus.addEventListener("change", loadDocuments);

function debounce(fn, delay) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

checkHealth();
loadDocuments();
