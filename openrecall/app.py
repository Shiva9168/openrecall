"""Flask web application and REST API for OpenRecall timeline and search UX."""

from threading import Thread
from typing import Dict, Any, List

from flask import Flask, jsonify, render_template_string, request, send_from_directory
from jinja2 import BaseLoader

from openrecall.config import appdata_folder, screenshots_path
from openrecall.database import (
    create_db,
    get_recent_entries,
    get_timeline_entries,
    get_timestamps,
    search_entries,
)
from openrecall.screenshot import record_screenshots_thread
from openrecall.utils import human_readable_time, timestamp_to_human_readable

app = Flask(__name__)

app.jinja_env.filters["human_readable_time"] = human_readable_time
app.jinja_env.filters["timestamp_to_human_readable"] = timestamp_to_human_readable

base_template = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>OpenRecall - Digital Memory</title>
  <link href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css" rel="stylesheet">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css">
  <style>
    body { background-color: #f8f9fa; color: #212529; }
    .navbar { box-shadow: 0 2px 4px rgba(0,0,0,0.08); }
    .timeline-card { transition: transform 0.15s ease-in-out, box-shadow 0.15s ease-in-out; border-radius: 8px; overflow: hidden; }
    .timeline-card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.12); }
    .badge-app { background-color: #e9ecef; color: #495057; font-weight: 500; }
    .text-snippet { font-size: 0.85rem; color: #6c757d; max-height: 3.6em; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
    .modal-img { max-height: 80vh; object-fit: contain; }
  </style>
</head>
<body>
<nav class="navbar navbar-expand-lg navbar-light bg-white mb-4">
  <div class="container">
    <a class="navbar-brand font-weight-bold" href="/">
      <i class="bi bi-clock-history text-primary mr-1"></i> OpenRecall
    </a>
    <form class="form-inline my-2 my-lg-0 flex-grow-1 mx-lg-4" action="/search" method="get">
      <div class="input-group w-100">
        <input class="form-control" type="search" name="q" value="{{ request.args.get('q', '') }}" placeholder="Search local digital memory..." aria-label="Search">
        <div class="input-group-append">
          <button class="btn btn-primary" type="submit">
            <i class="bi bi-search"></i> Search
          </button>
        </div>
      </div>
    </form>
  </div>
</nav>

<div class="container pb-5">
  {% block content %}{% endblock %}
</div>

<script src="https://code.jquery.com/jquery-3.5.1.slim.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@popperjs/core@2.5.3/dist/umd/popper.min.js"></script>
<script src="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/js/bootstrap.min.js"></script>
</body>
</html>
"""


class StringLoader(BaseLoader):
    def get_source(self, environment, template):
        if template == "base_template":
            return base_template, None, lambda: True
        return None, None, None


app.jinja_env.loader = StringLoader()


def _get_pagination_params() -> tuple[int, int, int]:
    """Helper to safely parse page, limit, and offset query parameters."""
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1

    try:
        limit = min(max(1, int(request.args.get("limit", 50))), 200)
    except (ValueError, TypeError):
        limit = 50

    offset = (page - 1) * limit
    return page, limit, offset


def _entry_to_dict(entry) -> Dict[str, Any]:
    """Serializes a database Entry into a clean JSON-friendly dictionary."""
    text_val = entry.text or ""
    snippet = text_val[:200] + ("..." if len(text_val) > 200 else "")
    return {
        "id": entry.id,
        "timestamp": entry.timestamp,
        "human_time": timestamp_to_human_readable(entry.timestamp),
        "app": entry.app or "Unknown App",
        "title": entry.title or "Unknown Title",
        "image_path": entry.image_path or f"{entry.timestamp}_0.webp",
        "text_snippet": snippet,
        "platform": entry.platform,
        "monitor": entry.monitor,
    }


@app.route("/")
def timeline():
    """Renders the paginated timeline history view."""
    page, limit, offset = _get_pagination_params()
    app_filter = request.args.get("app")
    title_filter = request.args.get("title")

    entries = get_timeline_entries(
        app=app_filter,
        title=title_filter,
        limit=limit,
        offset=offset,
    )

    return render_template_string(
        """
{% extends "base_template" %}
{% block content %}
<div class="d-flex justify-content-between align-items-center mb-4">
  <h4 class="mb-0 font-weight-bold">Timeline History</h4>
  <span class="text-muted small">Showing page {{ page }} ({{ entries|length }} items)</span>
</div>

{% if entries|length > 0 %}
  <div class="row">
    {% for entry in entries %}
      <div class="col-12 col-md-6 col-lg-4 mb-4">
        <div class="card timeline-card h-100 bg-white">
          <a href="#" data-toggle="modal" data-target="#modal-{{ loop.index0 }}">
            <img src="/static/{{ entry.image_path or (entry.timestamp|string + '_0.webp') }}" class="card-img-top" alt="Screenshot" style="height: 180px; object-fit: cover;">
          </a>
          <div class="card-body p-3 d-flex flex-column justify-content-between">
            <div>
              <div class="d-flex justify-content-between align-items-center mb-1">
                <span class="badge badge-app text-truncate" style="max-width: 140px;">{{ entry.app or 'Unknown App' }}</span>
                <span class="text-muted small">{{ entry.timestamp | timestamp_to_human_readable }}</span>
              </div>
              <h6 class="card-title text-truncate mb-2" title="{{ entry.title }}">{{ entry.title or 'Untitled Window' }}</h6>
              {% if entry.text %}
                <p class="text-snippet mb-0">{{ entry.text }}</p>
              {% endif %}
            </div>
          </div>
        </div>
      </div>

      <!-- Preview Modal -->
      <div class="modal fade" id="modal-{{ loop.index0 }}" tabindex="-1" role="dialog" aria-hidden="true">
        <div class="modal-dialog modal-xl modal-dialog-centered" role="document">
          <div class="modal-content">
            <div class="modal-header">
              <h5 class="modal-title text-truncate">{{ entry.app }} &mdash; {{ entry.title }}</h5>
              <button type="button" class="close" data-dismiss="modal" aria-label="Close">
                <span aria-hidden="true">&times;</span>
              </button>
            </div>
            <div class="modal-body text-center bg-dark p-2">
              <img src="/static/{{ entry.image_path or (entry.timestamp|string + '_0.webp') }}" class="img-fluid modal-img" alt="Full Screenshot">
            </div>
            {% if entry.text %}
              <div class="modal-footer justify-content-start bg-light">
                <div class="w-100">
                  <strong class="d-block small text-uppercase text-muted mb-1">Extracted OCR Text:</strong>
                  <pre class="mb-0 bg-white p-2 border rounded text-dark small" style="max-height: 150px; overflow-y: auto;">{{ entry.text }}</pre>
                </div>
              </div>
            {% endif %}
          </div>
        </div>
      </div>
    {% endfor %}
  </div>

  <!-- Pagination Controls -->
  <nav aria-label="Timeline pagination" class="mt-3">
    <ul class="pagination justify-content-center">
      {% if page > 1 %}
        <li class="page-item">
          <a class="page-link" href="/?page={{ page - 1 }}&limit={{ limit }}{% if request.args.get('app') %}&app={{ request.args.get('app') }}{% endif %}">Previous</a>
        </li>
      {% else %}
        <li class="page-item disabled"><span class="page-link">Previous</span></li>
      {% endif %}
      <li class="page-item active"><span class="page-link">{{ page }}</span></li>
      {% if entries|length == limit %}
        <li class="page-item">
          <a class="page-link" href="/?page={{ page + 1 }}&limit={{ limit }}{% if request.args.get('app') %}&app={{ request.args.get('app') }}{% endif %}">Next</a>
        </li>
      {% else %}
        <li class="page-item disabled"><span class="page-link">Next</span></li>
      {% endif %}
    </ul>
  </nav>

{% else %}
  <div class="alert alert-info py-4 text-center" role="alert">
    <i class="bi bi-info-circle display-4 d-block mb-2 text-info"></i>
    <h5 class="alert-heading">No timeline records found</h5>
    <p class="mb-0">No desktop screen captures have been recorded yet or match your active filter.</p>
  </div>
{% endif %}
{% endblock %}
""",
        entries=entries,
        page=page,
        limit=limit,
    )


@app.route("/search")
def search():
    """Renders the paginated search results view."""
    q = request.args.get("q", "")
    page, limit, offset = _get_pagination_params()
    app_filter = request.args.get("app")
    title_filter = request.args.get("title")

    matching_entries = search_entries(
        query=q,
        app=app_filter,
        title=title_filter,
        limit=limit,
        offset=offset,
    )

    return render_template_string(
        """
{% extends "base_template" %}
{% block content %}
<div class="d-flex justify-content-between align-items-center mb-4">
  <h4 class="mb-0 font-weight-bold">
    {% if q %}Search Results for &ldquo;{{ q }}&rdquo;{% else %}Search All Records{% endif %}
  </h4>
  <span class="text-muted small">Page {{ page }} ({{ entries|length }} matches)</span>
</div>

{% if entries|length > 0 %}
  <div class="row">
    {% for entry in entries %}
      <div class="col-12 col-md-6 col-lg-4 mb-4">
        <div class="card timeline-card h-100 bg-white">
          <a href="#" data-toggle="modal" data-target="#modal-search-{{ loop.index0 }}">
            <img src="/static/{{ entry.image_path or (entry.timestamp|string + '_0.webp') }}" class="card-img-top" alt="Screenshot" style="height: 180px; object-fit: cover;">
          </a>
          <div class="card-body p-3 d-flex flex-column justify-content-between">
            <div>
              <div class="d-flex justify-content-between align-items-center mb-1">
                <span class="badge badge-app text-truncate" style="max-width: 140px;">{{ entry.app or 'Unknown App' }}</span>
                <span class="text-muted small">{{ entry.timestamp | timestamp_to_human_readable }}</span>
              </div>
              <h6 class="card-title text-truncate mb-2" title="{{ entry.title }}">{{ entry.title or 'Untitled Window' }}</h6>
              {% if entry.text %}
                <p class="text-snippet mb-0">{{ entry.text }}</p>
              {% endif %}
            </div>
          </div>
        </div>
      </div>

      <!-- Preview Modal -->
      <div class="modal fade" id="modal-search-{{ loop.index0 }}" tabindex="-1" role="dialog" aria-hidden="true">
        <div class="modal-dialog modal-xl modal-dialog-centered" role="document">
          <div class="modal-content">
            <div class="modal-header">
              <h5 class="modal-title text-truncate">{{ entry.app }} &mdash; {{ entry.title }}</h5>
              <button type="button" class="close" data-dismiss="modal" aria-label="Close">
                <span aria-hidden="true">&times;</span>
              </button>
            </div>
            <div class="modal-body text-center bg-dark p-2">
              <img src="/static/{{ entry.image_path or (entry.timestamp|string + '_0.webp') }}" class="img-fluid modal-img" alt="Full Screenshot">
            </div>
            {% if entry.text %}
              <div class="modal-footer justify-content-start bg-light">
                <div class="w-100">
                  <strong class="d-block small text-uppercase text-muted mb-1">Extracted OCR Text:</strong>
                  <pre class="mb-0 bg-white p-2 border rounded text-dark small" style="max-height: 150px; overflow-y: auto;">{{ entry.text }}</pre>
                </div>
              </div>
            {% endif %}
          </div>
        </div>
      </div>
    {% endfor %}
  </div>

  <!-- Pagination Controls -->
  <nav aria-label="Search pagination" class="mt-3">
    <ul class="pagination justify-content-center">
      {% if page > 1 %}
        <li class="page-item">
          <a class="page-link" href="/search?q={{ q }}&page={{ page - 1 }}&limit={{ limit }}{% if request.args.get('app') %}&app={{ request.args.get('app') }}{% endif %}">Previous</a>
        </li>
      {% else %}
        <li class="page-item disabled"><span class="page-link">Previous</span></li>
      {% endif %}
      <li class="page-item active"><span class="page-link">{{ page }}</span></li>
      {% if entries|length == limit %}
        <li class="page-item">
          <a class="page-link" href="/search?q={{ q }}&page={{ page + 1 }}&limit={{ limit }}{% if request.args.get('app') %}&app={{ request.args.get('app') }}{% endif %}">Next</a>
        </li>
      {% else %}
        <li class="page-item disabled"><span class="page-link">Next</span></li>
      {% endif %}
    </ul>
  </nav>

{% else %}
  <div class="alert alert-warning py-4 text-center" role="alert">
    <i class="bi bi-exclamation-triangle display-4 d-block mb-2 text-warning"></i>
    <h5 class="alert-heading">No matching memory records</h5>
    <p class="mb-0">No records match your search query &ldquo;{{ q }}&rdquo;.</p>
  </div>
{% endif %}
{% endblock %}
""",
        entries=matching_entries,
        q=q,
        page=page,
        limit=limit,
    )


@app.route("/api/timeline")
def api_timeline():
    """REST API endpoint returning paginated timeline history as JSON."""
    page, limit, offset = _get_pagination_params()
    app_filter = request.args.get("app")
    title_filter = request.args.get("title")

    entries = get_timeline_entries(
        app=app_filter,
        title=title_filter,
        limit=limit,
        offset=offset,
    )

    data = [_entry_to_dict(e) for e in entries]
    return jsonify({
        "page": page,
        "limit": limit,
        "count": len(data),
        "entries": data,
    })


@app.route("/api/search")
def api_search():
    """REST API endpoint returning paginated search results as JSON."""
    q = request.args.get("q", "")
    page, limit, offset = _get_pagination_params()
    app_filter = request.args.get("app")
    title_filter = request.args.get("title")

    entries = search_entries(
        query=q,
        app=app_filter,
        title=title_filter,
        limit=limit,
        offset=offset,
    )

    data = [_entry_to_dict(e) for e in entries]
    return jsonify({
        "query": q,
        "page": page,
        "limit": limit,
        "count": len(data),
        "entries": data,
    })


@app.route("/static/<filename>")
def serve_image(filename):
    """Serves WebP screenshot files safely from the application screenshots directory."""
    return send_from_directory(screenshots_path, filename)


def main():
    create_db()
    print(f"Appdata folder: {appdata_folder}")
    t = Thread(target=record_screenshots_thread, daemon=True)
    t.start()
    app.run(port=8082)


if __name__ == "__main__":
    main()
