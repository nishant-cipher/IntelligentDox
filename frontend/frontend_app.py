"""Lightweight Flask server for the dashboard UI.

Serves static HTML/CSS/JS only. All document data comes from the backend
REST API at runtime via client-side fetch() calls - nothing here queries a
database or hardcodes results.
"""
import os

from flask import Flask, render_template

app = Flask(__name__, template_folder="templates", static_folder="static")

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")


@app.context_processor
def inject_config():
    return {"backend_url": BACKEND_URL}


@app.route("/")
def dashboard():
    return render_template("dashboard.html", backend_url=BACKEND_URL)


@app.route("/document/<path:document_name>")
def document_result(document_name: str):
    return render_template("document_result.html", backend_url=BACKEND_URL, document_name=document_name)


@app.route("/healthz")
def healthz():
    return {"status": "healthy"}


if __name__ == "__main__":
    port = int(os.environ.get("FRONTEND_PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("ENVIRONMENT", "development") == "development")
