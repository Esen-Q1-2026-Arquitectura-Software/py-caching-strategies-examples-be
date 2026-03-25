import os
import httpx
from flask import Flask, render_template, jsonify

app = Flask(__name__)

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")


@app.get("/")
def index():
    return render_template("index.html", backend_url=BACKEND_URL)


@app.get("/api/hello")
def proxy_hello():
    """Proxy the FastAPI root endpoint and return it to the template via AJAX."""
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{BACKEND_URL}/")
        return jsonify(resp.json())
    except httpx.RequestError as exc:
        return jsonify({"error": str(exc)}), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
