"""End-to-end API tests using FastAPI's TestClient against real dataset files."""
import io

from .conftest import dataset_file, skip_if_no_dataset


def test_health(client):
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] in {"healthy", "degraded"}


@skip_if_no_dataset
def test_process_invoice_end_to_end(client):
    path = dataset_file("Invoices", "X51005361895.jpg")
    with open(path, "rb") as fh:
        res = client.post(
            "/api/v1/documents/process",
            files={"file": (path.name, fh, "image/jpeg")},
            data={"document_type": "invoice"},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["document_name"] == path.name
    assert body["document_type"] == "invoice"
    assert body["processing_status"] in {"PASS", "FAILED"}
    assert "file_validation" in body
    assert "extracted_data" in body
    assert "validation" in body
    assert "processing_metadata" in body


@skip_if_no_dataset
def test_process_balance_sheet_end_to_end(client):
    path = dataset_file("Balance Sheet", "Consolidated Balance Sheet 2026.pdf")
    with open(path, "rb") as fh:
        res = client.post(
            "/api/v1/documents/process",
            files={"file": (path.name, fh, "application/pdf")},
            data={"document_type": "balance_sheet"},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["processing_status"] == "PASS"
    assert body["validation"]["overall_status"] == "PASS"
    assert body["extracted_data"]["totals"]["total_assets"]["2026"] > 0


@skip_if_no_dataset
def test_get_document_by_name_returns_latest(client):
    path = dataset_file("Balance Sheet", "Consolidated Balance Sheet 2026.pdf")
    with open(path, "rb") as fh:
        client.post(
            "/api/v1/documents/process",
            files={"file": (path.name, fh, "application/pdf")},
            data={"document_type": "balance_sheet"},
        )
    res = client.get(f"/api/v1/documents/{path.name}")
    assert res.status_code == 200
    assert res.json()["document_name"] == path.name


@skip_if_no_dataset
def test_list_documents(client):
    path = dataset_file("Balance Sheet", "Consolidated Balance Sheet 2026.pdf")
    with open(path, "rb") as fh:
        client.post(
            "/api/v1/documents/process",
            files={"file": (path.name, fh, "application/pdf")},
            data={"document_type": "balance_sheet"},
        )
    res = client.get("/api/v1/documents")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] >= 1
    assert any(d["document_name"] == path.name for d in body["documents"])


def test_get_nonexistent_document_returns_404(client):
    res = client.get("/api/v1/documents/does-not-exist.pdf")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"


def test_invalid_document_type_rejected(client):
    res = client.post(
        "/api/v1/documents/process",
        files={"file": ("x.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
        data={"document_type": "not_a_real_type"},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_DOCUMENT_TYPE"


def test_unsupported_file_type_rejected(client):
    res = client.post(
        "/api/v1/documents/process",
        files={"file": ("notes.txt", io.BytesIO(b"plain text content"), "text/plain")},
        data={"document_type": "invoice"},
    )
    assert res.status_code == 415
    assert res.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"


def test_empty_file_rejected(client):
    res = client.post(
        "/api/v1/documents/process",
        files={"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")},
        data={"document_type": "invoice"},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "EMPTY_FILE"


def test_error_response_never_leaks_stack_trace(client):
    res = client.post(
        "/api/v1/documents/process",
        files={"file": ("notes.txt", io.BytesIO(b"text"), "text/plain")},
        data={"document_type": "invoice"},
    )
    body = res.json()
    assert "Traceback" not in str(body)
    assert set(body.keys()) == {"error"}
