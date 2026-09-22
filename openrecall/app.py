"""Flask web application and REST API for OpenRecall timeline and search UX."""

import signal
import sys
from datetime import datetime, timezone
from threading import Thread
from typing import Dict, Any, List, Optional

from flask import Flask, jsonify, redirect, render_template_string, request, send_from_directory
from jinja2 import BaseLoader

from openrecall.config import appdata_folder, screenshots_path
from openrecall.database import (
    create_db,
    get_all_entries,
    get_available_apps,
    get_entry_nearest_timestamp,
    get_recent_entries,
    get_timeline_bounds,
    get_timeline_entries,
    get_timestamps,
    get_total_entries_count,
    reconcile_storage_and_database,
    search_entries,
)
from openrecall.maintenance import MaintenanceWorker
from openrecall.ocr import TesseractOCRProvider
from openrecall.privacy import get_privacy_policy
from openrecall.screenshot import get_capture_pipeline, record_screenshots_thread
from openrecall.utils import human_readable_time, timestamp_to_human_readable

app = Flask(__name__)

app.jinja_env.filters["human_readable_time"] = human_readable_time
app.jinja_env.filters["timestamp_to_human_readable"] = timestamp_to_human_readable


@app.context_processor
def inject_global_template_context():
    policy = get_privacy_policy()
    ocr_provider = TesseractOCRProvider()
    return {
        "is_paused": policy.is_paused(),
        "ocr_available": ocr_provider.is_available(),
        "appdata_folder": appdata_folder,
    }

base_template = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>OpenRecall - Digital Memory</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background-color: #f8f9fa; color: #212529; line-height: 1.5; }
    .container { width: 100%; max-width: 1140px; margin: 0 auto; padding: 0 15px; }
    .navbar { background-color: #ffffff; border-bottom: 1px solid #e9ecef; box-shadow: 0 2px 4px rgba(0,0,0,0.04); padding: 12px 0; margin-bottom: 24px; }
    .navbar .container { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; }
    .navbar-brand { font-weight: 700; font-size: 1.25rem; color: #0d6efd; text-decoration: none; display: flex; align-items: center; gap: 6px; }
    .search-form { display: flex; flex-grow: 1; max-width: 600px; gap: 8px; }
    .input-group { display: flex; width: 100%; }
    .form-control { flex: 1; padding: 8px 12px; font-size: 0.95rem; border: 1px solid #ced4da; border-radius: 6px 0 0 6px; outline: none; transition: border-color 0.15s ease; }
    .form-control:focus { border-color: #0d6efd; }
    .custom-select { padding: 8px 12px; font-size: 0.95rem; border: 1px solid #ced4da; border-radius: 6px; background-color: #fff; width: 100%; outline: none; }
    .btn { display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 8px 16px; font-size: 0.95rem; font-weight: 500; border-radius: 6px; border: 1px solid transparent; cursor: pointer; text-decoration: none; transition: all 0.15s ease; }
    .btn-primary { background-color: #0d6efd; color: #fff; border-color: #0d6efd; }
    .btn-primary:hover { background-color: #0b5ed7; border-color: #0a58ca; }
    .btn-outline-primary { background-color: transparent; color: #0d6efd; border-color: #0d6efd; }
    .btn-outline-primary:hover { background-color: #0d6efd; color: #fff; }
    .btn-outline-secondary { background-color: transparent; color: #6c757d; border-color: #6c757d; }
    .btn-outline-secondary:hover { background-color: #6c757d; color: #fff; }
    .btn-sm { padding: 4px 10px; font-size: 0.85rem; border-radius: 4px; }
    .btn-search { border-radius: 0 6px 6px 0; }

    .card { background-color: #fff; border: 1px solid #e9ecef; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
    .timeline-card { transition: transform 0.15s ease, box-shadow 0.15s ease; border-radius: 8px; overflow: hidden; height: 100%; display: flex; flex-direction: column; }
    .timeline-card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
    .timeline-card a { text-decoration: none; color: inherit; }
    .card-img-top { width: 100%; height: 180px; object-fit: cover; border-bottom: 1px solid #f1f3f5; }
    .card-body { padding: 16px; display: flex; flex-direction: column; justify-content: space-between; flex-grow: 1; }

    .badge { display: inline-block; padding: 3px 8px; font-size: 0.75rem; font-weight: 600; border-radius: 4px; text-transform: uppercase; }
    .badge-app { background-color: #e9ecef; color: #495057; }
    .badge-monitor { background-color: #d1ecf1; color: #0c5460; }
    .badge-info { background-color: #cff4fc; color: #055160; }
    .text-snippet { font-size: 0.85rem; color: #6c757d; max-height: 3.6em; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; margin-top: 6px; }

    .row { display: flex; flex-wrap: wrap; margin: 0 -10px; }
    .col-12 { flex: 0 0 100%; max-width: 100%; padding: 0 10px; }
    .col-6 { flex: 0 0 50%; max-width: 50%; padding: 0 10px; }
    .col-md-3 { flex: 0 0 25%; max-width: 25%; padding: 0 10px; }
    .col-md-6 { flex: 0 0 50%; max-width: 50%; padding: 0 10px; }
    .col-lg-4 { flex: 0 0 33.333333%; max-width: 33.333333%; padding: 0 10px; }
    .col-lg-8 { flex: 0 0 66.666667%; max-width: 66.666667%; padding: 0 10px; }

    @media (max-width: 768px) {
      .col-md-3, .col-md-6, .col-lg-4, .col-lg-8 { flex: 0 0 100%; max-width: 100%; }
      .form-row { flex-direction: column; gap: 10px; }
    }

    .form-row { display: flex; flex-wrap: wrap; align-items: center; margin: 0 -5px; gap: 8px; }
    .form-row > div { padding: 0 5px; }
    .card-body-filter { padding: 16px; margin-bottom: 24px; }

    .pagination { display: flex; list-style: none; padding: 0; justify-content: center; gap: 4px; margin-top: 24px; }
    .page-item { display: inline-block; }
    .page-link { display: inline-block; padding: 6px 12px; border: 1px solid #dee2e6; border-radius: 4px; color: #0d6efd; text-decoration: none; background-color: #fff; }
    .page-item.active .page-link { background-color: #0d6efd; color: #fff; border-color: #0d6efd; }
    .page-item.disabled .page-link { color: #6c757d; pointer-events: none; background-color: #e9ecef; }

    .alert { padding: 16px; border-radius: 6px; margin-bottom: 20px; }
    .alert-info { background-color: #cff4fc; color: #055160; border: 1px solid #b6effb; }
    .alert-warning { background-color: #fff3cd; color: #664d03; border: 1px solid #ffecb5; }

    .icon-svg { width: 1.1em; height: 1.1em; fill: currentColor; vertical-align: -0.15em; }
    pre { font-family: SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
  </style>
</head>
<body>
<nav class="navbar">
  <div class="container">
    <a class="navbar-brand" href="/">
      <svg class="icon-svg" viewBox="0 0 16 16"><path d="M8 3.5a.5.5 0 0 0-1 0V9a.5.5 0 0 0 .252.434l3.5 2a.5.5 0 0 0 .496-.868L8 8.71V3.5z"/><path d="M8 16A8 8 0 1 0 8 0a8 8 0 0 0 0 16zm7-8A7 7 0 1 1 1 8a7 7 0 0 1 14 0z"/></svg>
      OpenRecall
    </a>
    <div class="d-flex align-items-center gap-2 my-1 my-md-0">
      {% if is_paused %}
        <span class="badge" style="background-color: #fff3cd; color: #664d03; border: 1px solid #ffecb5; padding: 5px 9px;">Recording Paused</span>
        <form action="/api/resume" method="post" style="display:inline; margin:0;">
          <button type="submit" class="btn btn-sm btn-outline-primary py-1 px-2" style="font-size: 0.8rem;">Resume</button>
        </form>
      {% else %}
        <span class="badge" style="background-color: #d1e7dd; color: #0f5132; border: 1px solid #badbcc; padding: 5px 9px;">Recording Active</span>
        <form action="/api/pause" method="post" style="display:inline; margin:0;">
          <button type="submit" class="btn btn-sm btn-outline-secondary py-1 px-2" style="font-size: 0.8rem;">Pause</button>
        </form>
      {% endif %}
    </div>
    <form class="search-form" action="/search" method="get">
      <div class="input-group">
        <input class="form-control" type="search" name="q" value="{{ request.args.get('q', '') }}" placeholder="Search local digital memory..." aria-label="Search">
        <button class="btn btn-primary btn-search" type="submit">
          <svg class="icon-svg" viewBox="0 0 16 16"><path d="M11.742 10.344a6.5 6.5 0 1 0-1.397 1.398h-.001c.03.04.062.078.098.115l3.85 3.85a1 1 0 0 0 1.415-1.414l-3.85-3.85a1.007 1.007 0 0 0-.115-.1zM12 6.5a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0z"/></svg>
          Search
        </button>
      </div>
    </form>
  </div>
</nav>

<div class="container pb-5">
  {% block content %}{% endblock %}
</div>
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


def _parse_date_to_timestamp(date_str: Optional[str], end_of_day: bool = False) -> Optional[int]:
    """Parses an HTML <input type="date"> string (YYYY-MM-DD) to a system Unix timestamp."""
    if not date_str or not date_str.strip():
        return None
    try:
        dt = datetime.strptime(date_str.strip(), "%Y-%m-%d")
        if end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59)
        else:
            dt = dt.replace(hour=0, minute=0, second=0)
        return int(dt.timestamp())
    except Exception:
        return None



def _entry_to_dict(entry) -> Dict[str, Any]:
    """Serializes a database Entry into a clean JSON-friendly dictionary."""
    text_val = entry.text or ""
    snippet = text_val[:200] + ("..." if len(text_val) > 200 else "")
    img_name = entry.image_path or f"{entry.timestamp}_0.webp"
    return {
        "id": entry.id,
        "timestamp": entry.timestamp,
        "human_time": timestamp_to_human_readable(entry.timestamp),
        "app": entry.app or "Unknown App",
        "title": entry.title or "Unknown Title",
        "image_path": img_name,
        "image_url": f"/screenshot/{img_name}",
        "text_snippet": snippet,
        "text": text_val,
        "platform": entry.platform,
        "monitor": entry.monitor,
    }


@app.route("/")
def timeline():
    """Renders the timeline view mode with range slider or gallery mode grid."""
    mode = request.args.get("mode", "timeline")
    page, limit, offset = _get_pagination_params()

    bounds = get_timeline_bounds()
    total_count = bounds.get("total_count", 0)

    if mode == "gallery":
        app_filter = request.args.get("app")
        title_filter = request.args.get("title")
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")

        start_ts = _parse_date_to_timestamp(start_date, end_of_day=False)
        end_ts = _parse_date_to_timestamp(end_date, end_of_day=True)

        available_apps = get_available_apps()
        entries = get_timeline_entries(
            start_time=start_ts,
            end_time=end_ts,
            app=app_filter,
            title=title_filter,
            limit=limit,
            offset=offset,
        )

        return render_template_string(
            """
{% extends "base_template" %}
{% block content %}
<div class="d-flex justify-content-between align-items-center mb-3">
  <h4 class="mb-0 font-weight-bold">Digital Memory Gallery</h4>
  <div class="btn-group">
    <a href="/?mode=timeline" class="btn btn-outline-primary btn-sm">Timeline View</a>
    <a href="/?mode=gallery" class="btn btn-primary btn-sm">Gallery Grid</a>
  </div>
</div>

{% if entries|length > 0 %}
  <div class="row">
    {% for entry in entries %}
      <div class="col-12 col-md-6 col-lg-4 mb-4">
        <div class="card timeline-card h-100 bg-white border-0 shadow-sm">
          <a href="/capture/{{ entry.id }}">
            <img src="/screenshot/{{ entry.image_path or (entry.timestamp|string + '_0.webp') }}" class="card-img-top" alt="Screenshot" style="height: 180px; object-fit: cover;">
          </a>
          <div class="card-body p-3 d-flex flex-column justify-content-between">
            <div>
              <div class="d-flex justify-content-between align-items-center mb-1">
                <span class="text-muted small ml-auto">{{ entry.timestamp | timestamp_to_human_readable }}</span>
              </div>
              {% if entry.text %}
                <p class="text-snippet mb-0">{{ entry.text }}</p>
              {% endif %}
            </div>
          </div>
        </div>
      </div>
    {% endfor %}
  </div>

  <!-- Pagination Controls -->
  <nav aria-label="Gallery pagination" class="mt-3">
    <ul class="pagination justify-content-center">
      {% if page > 1 %}
        <li class="page-item">
          <a class="page-link" href="/?mode=gallery&page={{ page - 1 }}&limit={{ limit }}">Previous</a>
        </li>
      {% else %}
        <li class="page-item disabled"><span class="page-link">Previous</span></li>
      {% endif %}
      <li class="page-item active"><span class="page-link">{{ page }}</span></li>
      {% if entries|length == limit %}
        <li class="page-item">
          <a class="page-link" href="/?mode=gallery&page={{ page + 1 }}&limit={{ limit }}">Next</a>
        </li>
      {% else %}
        <li class="page-item disabled"><span class="page-link">Next</span></li>
      {% endif %}
    </ul>
  </nav>

{% else %}
  <div class="alert alert-info py-4 text-center border-0 shadow-sm" role="alert">
    <i class="bi bi-info-circle display-4 d-block mb-2 text-info"></i>
    <h5 class="alert-heading">No timeline records found</h5>
    <p class="mb-0">No desktop screen captures available.</p>
  </div>
{% endif %}
{% endblock %}
""",
            entries=entries,
            available_apps=available_apps,
            page=page,
            limit=limit,
            total_count=total_count,
        )

    # Default: Timeline mode with Range Slider
    latest_ts = bounds.get("latest_ts")
    earliest_ts = bounds.get("earliest_ts")
    latest_entry = get_entry_nearest_timestamp(latest_ts) if latest_ts is not None else None

    return render_template_string(
        """
{% extends "base_template" %}
{% block content %}
<div class="d-flex justify-content-between align-items-center mb-3">
  <h4 class="mb-0 font-weight-bold">Timeline History</h4>
  <div class="btn-group">
    <a href="/?mode=timeline" class="btn btn-primary btn-sm">Timeline View</a>
    <a href="/?mode=gallery" class="btn btn-outline-primary btn-sm">Gallery Grid</a>
  </div>
</div>

{% if total_count > 0 and latest_entry %}
  <div class="card card-body bg-white shadow-sm border-0 mb-4 p-4">
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h5 id="timelineTimeLabel" class="font-weight-bold text-primary mb-0">
        {{ latest_entry.timestamp | timestamp_to_human_readable }}
      </h5>
      <span class="text-muted small">Total Captures: {{ total_count }}</span>
    </div>

    <!-- Timeline Horizontal Range Slider -->
    <div class="mb-4">
      <input type="range" class="form-range w-100" id="timelineSlider"
             min="{{ earliest_ts }}" max="{{ latest_ts }}" value="{{ latest_ts }}"
             style="width: 100%; cursor: pointer;">
      <div class="d-flex justify-content-between text-muted small mt-1">
        <span>Oldest: {{ earliest_ts | timestamp_to_human_readable }}</span>
        <span>Present: {{ latest_ts | timestamp_to_human_readable }}</span>
      </div>
    </div>

    <!-- Single Screenshot Image Display -->
    <div class="card border-0 shadow-sm overflow-hidden bg-dark text-center p-2 mb-3">
      <a id="timelineImgLink" href="/capture/{{ latest_entry.id }}">
        <img id="timelineImg" src="/screenshot/{{ latest_entry.image_path or (latest_entry.timestamp|string + '_0.webp') }}"
             class="img-fluid rounded" style="max-height: 65vh; width: auto; margin: 0 auto;" alt="Timeline Capture">
      </a>
    </div>

    <!-- OCR Text Snippet Panel -->
    <div id="timelineSnippet" class="p-3 bg-light border rounded text-muted small" style="white-space: pre-wrap; word-break: break-word;">
      {{ latest_entry.text or 'No OCR text extracted for this capture.' }}
    </div>
  </div>

  <script>
  (function() {
    const slider = document.getElementById('timelineSlider');
    const timeLabel = document.getElementById('timelineTimeLabel');
    const img = document.getElementById('timelineImg');
    const imgLink = document.getElementById('timelineImgLink');
    const snippet = document.getElementById('timelineSnippet');
    if (!slider) return;

    let debounceTimer = null;

    function formatTimestamp(ts) {
      const d = new Date(ts * 1000);
      return d.toLocaleString();
    }

    function fetchCaptureAt(ts) {
      fetch('/api/timeline/at?timestamp=' + ts)
        .then(res => res.json())
        .then(data => {
          if (data && data.entry) {
            const e = data.entry;
            if (timeLabel) timeLabel.innerText = e.human_time || formatTimestamp(e.timestamp);
            if (img) img.src = e.image_url || ('/screenshot/' + e.image_path);
            if (imgLink) imgLink.href = '/capture/' + e.id;
            if (snippet) snippet.innerText = e.text || e.text_snippet || 'No OCR text extracted for this capture.';
          }
        })
        .catch(err => console.error(err));
    }

    slider.addEventListener('input', function() {
      const val = parseInt(this.value, 10);
      if (timeLabel) timeLabel.innerText = formatTimestamp(val);
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function() {
        fetchCaptureAt(val);
      }, 100);
    });
  })();
  </script>

{% else %}
  <div class="card card-body bg-white shadow-sm p-4 text-center border-0 mb-4">
    <div class="mb-3">
      <svg class="icon-svg text-primary" style="width: 3rem; height: 3rem;" viewBox="0 0 16 16"><path d="M8 3.5a.5.5 0 0 0-1 0V9a.5.5 0 0 0 .252.434l3.5 2a.5.5 0 0 0 .496-.868L8 8.71V3.5z"/><path d="M8 16A8 8 0 1 0 8 0a8 8 0 0 0 0 16zm7-8A7 7 0 1 1 1 8a7 7 0 0 1 14 0z"/></svg>
    </div>
    <h4 class="font-weight-bold mb-2">Welcome to OpenRecall</h4>
    <p class="text-muted mx-auto mb-4" style="max-width: 600px;">
      OpenRecall is active and monitoring your desktop in the background. Visual snapshots will automatically appear here as screen changes occur.
    </p>

    <div class="row justify-content-center text-left mb-4">
      <div class="col-12 col-md-8">
        <ul class="list-group list-group-flush border rounded">
          <li class="list-group-item d-flex justify-content-between align-items-center">
            <span><strong class="text-dark">Capture Pipeline:</strong></span>
            {% if is_paused %}
              <span class="badge" style="background-color: #fff3cd; color: #664d03; border: 1px solid #ffecb5;">Paused</span>
            {% else %}
              <span class="badge" style="background-color: #d1e7dd; color: #0f5132; border: 1px solid #badbcc;">Recording Active</span>
            {% endif %}
          </li>
          <li class="list-group-item d-flex justify-content-between align-items-center">
            <span><strong class="text-dark">Tesseract OCR Engine:</strong></span>
            {% if ocr_available %}
              <span class="badge badge-info" style="background-color: #cff4fc; color: #055160;">Available (Text Indexing Active)</span>
            {% else %}
              <span class="badge" style="background-color: #fff3cd; color: #664d03;">Unavailable (Visual Only)</span>
            {% endif %}
          </li>
          <li class="list-group-item d-flex justify-content-between align-items-center">
            <span><strong class="text-dark">Local Data Directory:</strong></span>
            <code class="small text-truncate" style="max-width: 260px;" title="{{ appdata_folder }}">{{ appdata_folder }}</code>
          </li>
        </ul>
      </div>
    </div>
  </div>
{% endif %}
{% endblock %}
""",
        total_count=total_count,
        earliest_ts=earliest_ts,
        latest_ts=latest_ts,
        latest_entry=latest_entry,
    )


@app.route("/search")
def search():
    """Renders the paginated search results view with filter controls."""
    q = request.args.get("q", "")
    page, limit, offset = _get_pagination_params()
    app_filter = request.args.get("app")
    title_filter = request.args.get("title")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    start_ts = _parse_date_to_timestamp(start_date, end_of_day=False)
    end_ts = _parse_date_to_timestamp(end_date, end_of_day=True)

    available_apps = get_available_apps()
    matching_entries = search_entries(
        query=q,
        app=app_filter,
        title=title_filter,
        start_time=start_ts,
        end_time=end_ts,
        limit=limit,
        offset=offset,
    )

    return render_template_string(
        """
{% extends "base_template" %}
{% block content %}
<div class="d-flex justify-content-between align-items-center mb-3">
  <h4 class="mb-0 font-weight-bold">
    {% if q %}Search Results for &ldquo;{{ q }}&rdquo;{% else %}Search All Records{% endif %}
  </h4>
  <span class="text-muted small">Page {{ page }} ({{ entries|length }} matches)</span>
</div>

<!-- Filter Bar -->
<form method="get" action="/search" class="card card-body bg-white shadow-sm mb-4 p-3 border-0">
  <input type="hidden" name="q" value="{{ q }}">
  <div class="form-row align-items-center">
    <div class="col-12 col-md-3 mb-2 mb-md-0">
      <select class="custom-select" name="app">
        <option value="">All Applications</option>
        {% for a in available_apps %}
          <option value="{{ a }}" {% if request.args.get('app') == a %}selected{% endif %}>{{ a }}</option>
        {% endfor %}
      </select>
    </div>
    <div class="col-6 col-md-3 mb-2 mb-md-0">
      <input type="date" class="form-control" name="start_date" value="{{ request.args.get('start_date', '') }}" placeholder="From Date">
    </div>
    <div class="col-6 col-md-3 mb-2 mb-md-0">
      <input type="date" class="form-control" name="end_date" value="{{ request.args.get('end_date', '') }}" placeholder="To Date">
    </div>
    <div class="col-12 col-md-3 d-flex">
      <button type="submit" class="btn btn-primary flex-grow-1 mr-2"><i class="bi bi-funnel"></i> Filter</button>
      <a href="/search?q={{ q }}" class="btn btn-outline-secondary">Reset</a>
    </div>
  </div>
</form>

{% if entries|length > 0 %}
  <div class="row">
    {% for entry in entries %}
      <div class="col-12 col-md-6 col-lg-4 mb-4">
        <div class="card timeline-card h-100 bg-white border-0 shadow-sm">
          <a href="/capture/{{ entry.id }}">
            <img src="/screenshot/{{ entry.image_path or (entry.timestamp|string + '_0.webp') }}" class="card-img-top" alt="Screenshot" style="height: 180px; object-fit: cover;">
          </a>
          <div class="card-body p-3 d-flex flex-column justify-content-between">
            <div>
              <div class="d-flex justify-content-between align-items-center mb-1">
                <span class="badge badge-app text-truncate" style="max-width: 140px;">{{ entry.app or 'Unknown App' }}</span>
                {% if entry.monitor and entry.monitor > 1 %}
                  <span class="badge badge-monitor ml-1">Mon {{ entry.monitor }}</span>
                {% endif %}
                <span class="text-muted small ml-auto">{{ entry.timestamp | timestamp_to_human_readable }}</span>
              </div>
              <h6 class="card-title text-truncate mb-2"><a href="/capture/{{ entry.id }}" class="text-dark" title="{{ entry.title }}">{{ entry.title or 'Untitled Window' }}</a></h6>
              {% if entry.text %}
                <p class="text-snippet mb-0">{{ entry.text }}</p>
              {% endif %}
            </div>
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
          <a class="page-link" href="/search?q={{ q }}&page={{ page - 1 }}&limit={{ limit }}{% if request.args.get('app') %}&app={{ request.args.get('app') }}{% endif %}{% if request.args.get('start_date') %}&start_date={{ request.args.get('start_date') }}{% endif %}{% if request.args.get('end_date') %}&end_date={{ request.args.get('end_date') }}{% endif %}">Previous</a>
        </li>
      {% else %}
        <li class="page-item disabled"><span class="page-link">Previous</span></li>
      {% endif %}
      <li class="page-item active"><span class="page-link">{{ page }}</span></li>
      {% if entries|length == limit %}
        <li class="page-item">
          <a class="page-link" href="/search?q={{ q }}&page={{ page + 1 }}&limit={{ limit }}{% if request.args.get('app') %}&app={{ request.args.get('app') }}{% endif %}{% if request.args.get('start_date') %}&start_date={{ request.args.get('start_date') }}{% endif %}{% if request.args.get('end_date') %}&end_date={{ request.args.get('end_date') }}{% endif %}">Next</a>
        </li>
      {% else %}
        <li class="page-item disabled"><span class="page-link">Next</span></li>
      {% endif %}
    </ul>
  </nav>

{% else %}
  <div class="alert alert-warning py-4 text-center border-0 shadow-sm" role="alert">
    <i class="bi bi-exclamation-triangle display-4 d-block mb-2 text-warning"></i>
    <h5 class="alert-heading">No matching memory records</h5>
    <p class="mb-0">No records match your search query &ldquo;{{ q }}&rdquo;.</p>
  </div>
{% endif %}
{% endblock %}
""",
        entries=matching_entries,
        available_apps=available_apps,
        q=q,
        page=page,
        limit=limit,
    )


@app.route("/capture/<int:entry_id>")
def capture_detail(entry_id: int):
    """Renders the detailed single-screenshot inspection page with OCR text panel and copy button."""
    from openrecall.database import get_entry_by_id

    entry = get_entry_by_id(entry_id)
    if not entry:
        return (
            render_template_string(
                """
{% extends "base_template" %}
{% block content %}
  <div class="alert alert-warning py-4 text-center border-0 shadow-sm mt-4" role="alert">
    <i class="bi bi-exclamation-triangle display-4 d-block mb-2 text-warning"></i>
    <h5 class="alert-heading">Capture Not Found</h5>
    <p class="mb-3">No screenshot record exists for database ID {{ entry_id }}.</p>
    <a href="/" class="btn btn-primary"><i class="bi bi-arrow-left"></i> Return to Timeline</a>
  </div>
{% endblock %}
""",
                entry_id=entry_id,
            ),
            404,
        )

    return render_template_string(
        """
{% extends "base_template" %}
{% block content %}
<div class="mb-3 d-flex justify-content-between align-items-center">
  <a href="/" class="btn btn-outline-secondary btn-sm">
    <i class="bi bi-arrow-left"></i> Back to Timeline
  </a>
  <div>
    {% if entry.id > 1 %}
      <a href="/capture/{{ entry.id - 1 }}" class="btn btn-outline-primary btn-sm mr-1"><i class="bi bi-chevron-left"></i> Previous</a>
    {% endif %}
    <a href="/capture/{{ entry.id + 1 }}" class="btn btn-outline-primary btn-sm">Next <i class="bi bi-chevron-right"></i></a>
  </div>
</div>

<div class="row">
  <div class="col-12 col-lg-8 mb-4">
    <div class="card border-0 shadow-sm overflow-hidden bg-dark text-center p-2">
      <img src="/screenshot/{{ entry.image_path or (entry.timestamp|string + '_0.webp') }}" class="img-fluid rounded" style="max-height: 75vh; width: auto; margin: 0 auto;" alt="Full Resolution Screenshot">
    </div>
  </div>

  <div class="col-12 col-lg-4 mb-4">
    <div class="card border-0 shadow-sm bg-white h-100">
      <div class="card-header bg-white font-weight-bold border-bottom d-flex justify-content-between align-items-center">
        <span><i class="bi bi-info-circle text-primary mr-1"></i> Capture Metadata</span>
        {% if entry.monitor and entry.monitor > 1 %}
          <span class="badge badge-info">Monitor {{ entry.monitor }}</span>
        {% endif %}
      </div>
      <div class="card-body">
        <dl class="row mb-0">
          <dt class="col-sm-4 text-muted small">Application</dt>
          <dd class="col-sm-8 font-weight-bold text-truncate" title="{{ entry.app }}">{{ entry.app or 'Unknown App' }}</dd>

          <dt class="col-sm-4 text-muted small">Window Title</dt>
          <dd class="col-sm-8 text-truncate" title="{{ entry.title }}">{{ entry.title or 'Untitled Window' }}</dd>

          <dt class="col-sm-4 text-muted small">Timestamp</dt>
          <dd class="col-sm-8">{{ entry.timestamp | timestamp_to_human_readable }}</dd>

          <dt class="col-sm-4 text-muted small">Database ID</dt>
          <dd class="col-sm-8">#{{ entry.id }}</dd>

          <dt class="col-sm-4 text-muted small">File Path</dt>
          <dd class="col-sm-8 text-monospace small text-truncate" title="{{ entry.image_path }}">{{ entry.image_path }}</dd>
        </dl>
        <hr>
        <div class="d-flex justify-content-between align-items-center mb-2">
          <strong class="small text-uppercase text-muted">Extracted OCR Text</strong>
          {% if entry.text %}
            <button class="btn btn-sm btn-outline-primary py-0" onclick="copyOcrText()"><i class="bi bi-clipboard"></i> Copy Text</button>
          {% endif %}
        </div>
        {% if entry.text %}
          <pre id="ocrTextBlock" class="bg-light p-3 border rounded small text-dark mb-0" style="max-height: 280px; overflow-y: auto; white-space: pre-wrap; word-break: break-word;">{{ entry.text }}</pre>
        {% else %}
          <p class="text-muted small italic mb-0">No OCR text extracted for this capture.</p>
        {% endif %}
      </div>
    </div>
  </div>
</div>

<script>
function copyOcrText() {
  const text = document.getElementById('ocrTextBlock').innerText;
  navigator.clipboard.writeText(text).then(() => {
    alert('OCR text copied to clipboard!');
  }).catch(err => {
    console.error('Failed to copy OCR text: ', err);
  });
}
</script>
{% endblock %}
""",
        entry=entry,
    )


@app.route("/api/capture/<int:entry_id>")
def api_capture_detail(entry_id: int):
    """REST API endpoint returning detailed metadata for a single capture ID as JSON."""
    from openrecall.database import get_entry_by_id

    entry = get_entry_by_id(entry_id)
    if not entry:
        return jsonify({"error": "Capture not found", "entry_id": entry_id}), 404
    return jsonify(_entry_to_dict(entry))


@app.route("/api/timeline")
def api_timeline():
    """REST API endpoint returning paginated timeline history as JSON with filter support."""
    page, limit, offset = _get_pagination_params()
    app_filter = request.args.get("app")
    title_filter = request.args.get("title")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    start_ts = _parse_date_to_timestamp(start_date, end_of_day=False)
    end_ts = _parse_date_to_timestamp(end_date, end_of_day=True)

    entries = get_timeline_entries(
        start_time=start_ts,
        end_time=end_ts,
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


@app.route("/api/timeline/bounds")
def api_timeline_bounds():
    """REST API endpoint returning earliest timestamp, latest timestamp, and total count."""
    bounds = get_timeline_bounds()
    return jsonify(bounds)


@app.route("/api/timeline/at")
def api_timeline_at():
    """REST API endpoint returning the single capture entry nearest to requested timestamp."""
    ts_arg = request.args.get("timestamp")
    if not ts_arg:
        return jsonify({"error": "Missing timestamp parameter"}), 400
    try:
        target_ts = int(float(ts_arg))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid timestamp parameter"}), 400

    entry = get_entry_nearest_timestamp(target_ts)
    if not entry:
        return jsonify({"entry": None})
    return jsonify({"entry": _entry_to_dict(entry)})


@app.route("/api/search")
def api_search():
    """REST API endpoint returning paginated search results as JSON with filter support."""
    q = request.args.get("q", "")
    page, limit, offset = _get_pagination_params()
    app_filter = request.args.get("app")
    title_filter = request.args.get("title")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    start_ts = _parse_date_to_timestamp(start_date, end_of_day=False)
    end_ts = _parse_date_to_timestamp(end_date, end_of_day=True)

    entries = search_entries(
        query=q,
        app=app_filter,
        title=title_filter,
        start_time=start_ts,
        end_time=end_ts,
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


@app.route("/api/health")
def api_health():
    """REST API endpoint returning application operational health metrics as JSON."""
    pipeline = get_capture_pipeline()
    return jsonify(pipeline.get_health_status())


@app.route("/api/pause", methods=["POST"])
def api_pause():
    """REST API endpoint to pause screen capture recording."""
    policy = get_privacy_policy()
    policy.pause()
    if request.is_json or request.headers.get("Accept") == "application/json" or request.args.get("format") == "json":
        return jsonify({"status": "paused", "is_paused": True})
    return redirect(request.referrer or "/")


@app.route("/api/resume", methods=["POST"])
def api_resume():
    """REST API endpoint to resume screen capture recording."""
    policy = get_privacy_policy()
    policy.resume()
    if request.is_json or request.headers.get("Accept") == "application/json" or request.args.get("format") == "json":
        return jsonify({"status": "active", "is_paused": False})
    return redirect(request.referrer or "/")


@app.route("/screenshot/<filename>")
def serve_image(filename):
    """Serves WebP screenshot files safely from active and historical appdata locations."""
    import os
    from openrecall.config import get_appdata_folder, screenshots_path

    active_file = os.path.join(screenshots_path, filename)
    if os.path.exists(active_file):
        return send_from_directory(screenshots_path, filename)

    try:
        default_folder = get_appdata_folder()
        default_screenshots = os.path.join(default_folder, "screenshots")
        if os.path.normpath(default_screenshots) != os.path.normpath(screenshots_path):
            hist_file = os.path.join(default_screenshots, filename)
            if os.path.exists(hist_file):
                return send_from_directory(default_screenshots, filename)
    except Exception:
        pass

    return jsonify({"error": "Screenshot file not found"}), 404


@app.route("/static/<filename>")
def serve_static_legacy(filename):
    """Legacy static route fallback for screenshot images."""
    if filename.endswith(".webp"):
        return serve_image(filename)
    return send_from_directory(app.static_folder or screenshots_path, filename)


def main():
    from openrecall.config import args
    from openrecall.platform import get_platform_provider

    create_db()
    print(f"Appdata folder: {appdata_folder}")

    if getattr(args, "enable_autostart", False):
        if get_platform_provider().enable_startup():
            print("Successfully enabled system autostart.")
        else:
            print("Failed to enable system autostart.")

    if getattr(args, "disable_autostart", False):
        if get_platform_provider().disable_startup():
            print("Successfully disabled system autostart.")
        else:
            print("Failed to disable system autostart.")

    # 1. Run startup storage maintenance & orphan reconciliation
    print("Running startup storage reconciliation...")
    reconcile_storage_and_database()

    # 2. Start CapturePipeline
    pipeline = get_capture_pipeline()
    pipeline.start()

    # 3. Start MaintenanceWorker
    maintenance_worker = MaintenanceWorker(storage_lock=pipeline.storage_lock)
    maintenance_worker.start()

    # 4. Graceful OS signal handling (SIGINT, SIGTERM)
    def signal_handler(sig, frame):
        print("\nShutdown signal received. Stopping background threads gracefully...")
        pipeline.stop(timeout=2.0)
        maintenance_worker.stop(timeout=2.0)
        sys.exit(0)

    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    except (ValueError, AttributeError):
        pass

    app.run(port=8082)


if __name__ == "__main__":
    main()
