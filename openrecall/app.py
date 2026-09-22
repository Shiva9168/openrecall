"""Flask web application and REST API for OpenRecall timeline and search UX."""

import os
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
    get_timeline_captures_index,
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
  <link rel="stylesheet" href="/static/css/output.css">
  <style>
    /* Baseline layout fallbacks */
    *, *::before, *::after { box-sizing: border-box; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background-color: #f8fafc; color: #0f172a; line-height: 1.5; }
    .timeline-slider { -webkit-appearance: none; appearance: none; width: 100%; height: 8px; border-radius: 9999px; background: #cbd5e1; outline: none; transition: background 0.15s ease-in-out; }
    .timeline-slider:hover { background: #94a3b8; }
    .timeline-slider::-webkit-slider-thumb { -webkit-appearance: none; appearance: none; width: 20px; height: 20px; border-radius: 50%; background: #4f46e5; border: 2px solid #ffffff; box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.15); cursor: pointer; transition: transform 0.1s ease, background-color 0.15s ease; }
    .timeline-slider::-webkit-slider-thumb:hover { background: #4338ca; transform: scale(1.15); }
    .timeline-slider::-moz-range-thumb { width: 20px; height: 20px; border-radius: 50%; background: #4f46e5; border: 2px solid #ffffff; box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.15); cursor: pointer; transition: transform 0.1s ease, background-color 0.15s ease; }
    .timeline-slider::-moz-range-thumb:hover { background: #4338ca; transform: scale(1.15); }
  </style>
</head>
<body class="bg-slate-50 text-slate-800 flex flex-col min-h-screen">
  <!-- Offline Header Navigation -->
  <header class="bg-white border-b border-slate-200 sticky top-0 z-30 shadow-xs">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
      <div class="flex items-center justify-between h-16 gap-4">
        
        <!-- Brand & Privacy Indicator -->
        <div class="flex items-center gap-3 shrink-0">
          <a href="/" class="flex items-center gap-2 text-slate-900 font-bold text-lg hover:text-indigo-600 transition-colors">
            <svg class="w-6 h-6 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path>
            </svg>
            <span>OpenRecall</span>
          </a>
          <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-600 border border-slate-200">
            Local &bull; Private
          </span>
        </div>

        <!-- Recording Status & Controls -->
        <div class="flex items-center gap-2">
          {% if is_paused %}
            <span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium bg-amber-50 text-amber-800 border border-amber-200">
              <span class="w-2 h-2 rounded-full bg-amber-500 animate-pulse"></span>
              <span>Recording Paused</span>
            </span>
            <form action="/api/resume" method="post" class="inline m-0">
              <button type="submit" class="inline-flex items-center px-3 py-1 rounded-md text-xs font-semibold bg-indigo-50 text-indigo-700 border border-indigo-200 hover:bg-indigo-100 transition-colors cursor-pointer">
                Resume
              </button>
            </form>
          {% else %}
            <span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium bg-emerald-50 text-emerald-800 border border-emerald-200">
              <span class="w-2 h-2 rounded-full bg-emerald-500"></span>
              <span>Recording Active</span>
            </span>
            <form action="/api/pause" method="post" class="inline m-0">
              <button type="submit" class="inline-flex items-center px-3 py-1 rounded-md text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200 transition-colors cursor-pointer">
                Pause
              </button>
            </form>
          {% endif %}
        </div>

        <!-- Integrated Search Bar -->
        <form class="flex-1 max-w-md hidden md:flex items-center" action="/search" method="get">
          <div class="relative w-full">
            <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-slate-400">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
              </svg>
            </div>
            <input class="w-full pl-9 pr-20 py-1.5 text-sm bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:bg-white transition-all" 
                   type="search" name="q" value="{{ request.args.get('q', '') }}" placeholder="Search local memory..." aria-label="Search">
            <button class="absolute inset-y-1 right-1 px-3 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold rounded-md transition-colors cursor-pointer" type="submit">
              Search
            </button>
          </div>
        </form>

      </div>
    </div>
  </header>

  <!-- Main Container -->
  <main class="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6">
    {% block content %}{% endblock %}
  </main>

  <!-- Offline Footer -->
  <footer class="bg-white border-t border-slate-200 py-4 mt-auto">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col sm:flex-row items-center justify-between gap-2 text-xs text-slate-500">
      <div>OpenRecall &bull; Privacy-First Digital Memory Assistant</div>
      <div>Stored locally on this system &bull; Offline &bull; No Telemetry</div>
    </div>
  </footer>
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
<div class="flex flex-col sm:flex-row items-start sm:items-center justify-between pb-4 border-b border-slate-200 mb-6 gap-4">
  <div>
    <h1 class="text-xl font-bold text-slate-900">Digital Memory Gallery</h1>
    <p class="text-xs text-slate-500 mt-0.5">Browsing screen capture history as a grid</p>
  </div>
  <div class="inline-flex rounded-lg p-1 bg-slate-200/70 border border-slate-200">
    <a href="/?mode=timeline" class="px-3 py-1.5 text-xs font-semibold rounded-md text-slate-700 hover:text-slate-900 transition-colors">Timeline View</a>
    <a href="/?mode=gallery" class="px-3 py-1.5 text-xs font-semibold rounded-md bg-white text-indigo-600 shadow-xs">Gallery Grid</a>
  </div>
</div>

{% if entries|length > 0 %}
  <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
    {% for entry in entries %}
      <div class="bg-white rounded-xl border border-slate-200 shadow-xs hover:shadow-md hover:border-slate-300 transition-all overflow-hidden flex flex-col group">
        <a href="/capture/{{ entry.id }}" class="block bg-slate-900 aspect-video overflow-hidden relative">
          <img src="/screenshot/{{ entry.image_path or (entry.timestamp|string + '_0.webp') }}" 
               loading="lazy"
               class="w-full h-full object-cover group-hover:scale-102 transition-transform duration-200" alt="Screenshot">
        </a>
        <div class="p-4 flex flex-col flex-1 justify-between gap-3">
          <div>
            <div class="flex items-center justify-between text-xs text-slate-500 mb-1">
              <span class="font-medium text-slate-700">{{ entry.timestamp | timestamp_to_human_readable }}</span>
              {% if entry.monitor and entry.monitor > 1 %}
                <span class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 text-[10px] font-semibold border border-slate-200">Mon {{ entry.monitor }}</span>
              {% endif %}
            </div>
            {% if entry.text %}
              <p class="text-xs text-slate-600 line-clamp-3 leading-relaxed mt-2 bg-slate-50 p-2.5 rounded-lg border border-slate-100 font-mono">{{ entry.text }}</p>
            {% endif %}
          </div>
          <div class="pt-2 border-t border-slate-100 flex items-center justify-between">
            <span class="text-[11px] text-slate-400 font-mono">#{{ entry.id }}</span>
            <a href="/capture/{{ entry.id }}" class="text-xs font-semibold text-indigo-600 hover:text-indigo-800 transition-colors flex items-center gap-1">
              Inspect &rarr;
            </a>
          </div>
        </div>
      </div>
    {% endfor %}
  </div>

  <!-- Pagination Controls -->
  <nav aria-label="Gallery pagination" class="mt-8 flex justify-center">
    <div class="inline-flex items-center gap-2">
      {% if page > 1 %}
        <a class="px-3 py-1.5 text-xs font-medium text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 transition-colors" href="/?mode=gallery&page={{ page - 1 }}&limit={{ limit }}">Previous</a>
      {% else %}
        <span class="px-3 py-1.5 text-xs font-medium text-slate-400 bg-slate-100 border border-slate-200 rounded-lg cursor-not-allowed">Previous</span>
      {% endif %}
      <span class="px-3 py-1.5 text-xs font-semibold text-indigo-600 bg-indigo-50 border border-indigo-200 rounded-lg">Page {{ page }}</span>
      {% if entries|length == limit %}
        <a class="px-3 py-1.5 text-xs font-medium text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 transition-colors" href="/?mode=gallery&page={{ page + 1 }}&limit={{ limit }}">Next</a>
      {% else %}
        <span class="px-3 py-1.5 text-xs font-medium text-slate-400 bg-slate-100 border border-slate-200 rounded-lg cursor-not-allowed">Next</span>
      {% endif %}
    </div>
  </nav>

{% else %}
  <div class="bg-white rounded-xl border border-slate-200 p-8 text-center max-w-md mx-auto my-8 shadow-xs">
    <div class="w-12 h-12 bg-slate-100 text-slate-400 rounded-full flex items-center justify-center mx-auto mb-3">
      <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"></path>
      </svg>
    </div>
    <h3 class="text-base font-bold text-slate-900 mb-1">No timeline records found</h3>
    <p class="text-xs text-slate-500 mb-0">No desktop screen captures available matching this criteria.</p>
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

    # Default: Timeline mode with Discrete Range Slider
    latest_ts = bounds.get("latest_ts")
    latest_entry = get_entry_nearest_timestamp(latest_ts) if latest_ts is not None else None

    return render_template_string(
        """
{% extends "base_template" %}
{% block content %}
<div class="flex flex-col sm:flex-row items-start sm:items-center justify-between pb-4 border-b border-slate-200 mb-6 gap-4">
  <div>
    <h1 class="text-xl font-bold text-slate-900">Timeline History</h1>
    <p class="text-xs text-slate-500 mt-0.5">Chronological screen capture moments</p>
  </div>
  <div class="inline-flex rounded-lg p-1 bg-slate-200/70 border border-slate-200">
    <a href="/" class="px-3 py-1.5 text-xs font-semibold rounded-md bg-white text-indigo-600 shadow-xs">Timeline View</a>
    <a href="/?mode=gallery" class="px-3 py-1.5 text-xs font-semibold rounded-md text-slate-700 hover:text-slate-900 transition-colors">Gallery Grid</a>
  </div>
</div>

{% if total_count > 0 and latest_entry %}
  <div class="bg-white rounded-xl border border-slate-200 p-5 shadow-xs mb-6 flex flex-col gap-5">
    <!-- Header Timestamp & Capture Counter -->
    <div class="flex items-center justify-between flex-wrap gap-2 pb-3 border-b border-slate-100">
      <div class="flex items-center gap-2">
        <svg class="w-5 h-5 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path>
        </svg>
        <h2 id="timelineTimeLabel" class="text-base font-bold text-slate-900">
          {{ latest_entry.timestamp | timestamp_to_human_readable }}
        </h2>
      </div>
      <span id="timelineCounter" class="inline-flex items-center px-3 py-1 rounded-md text-xs font-semibold bg-indigo-50 text-indigo-700 border border-indigo-200 font-mono">
        Capture {{ total_count }} of {{ total_count }}
      </span>
    </div>

    <!-- Timeline Discrete Range Slider -->
    <div class="space-y-2">
      <input type="range" class="timeline-slider" id="timelineSlider"
             min="0" max="{{ total_count - 1 }}" value="{{ total_count - 1 }}" step="1"
             aria-label="Timeline navigation slider">
      <div class="flex items-center justify-between text-[11px] text-slate-500 font-medium">
        <span>Oldest Capture</span>
        <span class="text-indigo-600 flex items-center gap-1 font-semibold">
          <span>&larr;</span> <span>Use arrow keys to step frame-by-frame</span> <span>&rarr;</span>
        </span>
        <span>Latest Capture</span>
      </div>
    </div>

    <!-- Single Screenshot Image Display Frame -->
    <div class="relative bg-slate-950 rounded-xl overflow-hidden p-2 shadow-inner border border-slate-900 flex items-center justify-center min-h-[300px]">
      <a id="timelineImgLink" href="/capture/{{ latest_entry.id }}" class="block max-w-full" title="Click for capture details">
        <img id="timelineImg" src="/screenshot/{{ latest_entry.image_path or (latest_entry.timestamp|string + '_0.webp') }}"
             class="max-h-[65vh] w-auto h-auto object-contain rounded transition-opacity duration-150 mx-auto" alt="Timeline Capture">
      </a>
    </div>

    <!-- Extracted Text Snippet Panel -->
    <div class="bg-slate-50 rounded-lg p-4 border border-slate-200">
      <div class="flex items-center justify-between mb-2">
        <span class="text-xs font-bold text-slate-700 uppercase tracking-wider">Extracted Text</span>
      </div>
      <div id="timelineSnippet" class="text-xs text-slate-700 font-mono whitespace-pre-wrap break-words leading-relaxed max-h-40 overflow-y-auto">
        {{ latest_entry.text or 'No OCR text extracted for this capture.' }}
      </div>
    </div>
  </div>

  <script>
  (function() {
    const slider = document.getElementById('timelineSlider');
    const timeLabel = document.getElementById('timelineTimeLabel');
    const counterLabel = document.getElementById('timelineCounter');
    const img = document.getElementById('timelineImg');
    const imgLink = document.getElementById('timelineImgLink');
    const snippet = document.getElementById('timelineSnippet');
    if (!slider) return;

    let capturesIndex = [];
    let activeReqId = 0;
    let debounceTimer = null;

    function formatTimestamp(ts) {
      if (!ts) return '';
      const d = new Date(ts * 1000);
      return d.toLocaleString();
    }

    function renderCapture(entry) {
      if (!entry) return;
      if (timeLabel) timeLabel.innerText = entry.human_time || formatTimestamp(entry.timestamp);
      if (img) img.src = entry.image_url || ('/screenshot/' + entry.image_path);
      if (imgLink) imgLink.href = '/capture/' + entry.id;
      if (snippet) snippet.innerText = entry.text || entry.text_snippet || 'No OCR text extracted for this capture.';
    }

    function fetchCaptureForIndex(idx) {
      if (!capturesIndex || capturesIndex.length === 0) return;
      const reqId = ++activeReqId;
      const item = capturesIndex[idx];
      if (!item) return;

      fetch('/api/timeline/at?timestamp=' + item.timestamp)
        .then(res => res.json())
        .then(data => {
          if (reqId !== activeReqId) return; // Stale response protection
          if (data && data.entry) {
            renderCapture(data.entry);
          }
        })
        .catch(err => console.error(err));
    }

    function updateSliderPosition(idx) {
      if (!capturesIndex || capturesIndex.length === 0) return;
      const boundedIdx = Math.max(0, Math.min(idx, capturesIndex.length - 1));
      const item = capturesIndex[boundedIdx];
      if (item) {
        if (timeLabel) timeLabel.innerText = formatTimestamp(item.timestamp);
        if (counterLabel) counterLabel.innerText = 'Capture ' + (boundedIdx + 1) + ' of ' + capturesIndex.length;
      }
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function() {
        fetchCaptureForIndex(boundedIdx);
      }, 80);
    }

    // Fetch capture index list on load
    fetch('/api/timeline/index')
      .then(res => res.json())
      .then(data => {
        if (data && data.captures && data.captures.length > 0) {
          capturesIndex = data.captures;
          slider.max = capturesIndex.length - 1;
          slider.value = capturesIndex.length - 1;
          updateSliderPosition(capturesIndex.length - 1);
        }
      })
      .catch(err => console.error(err));

    slider.addEventListener('input', function() {
      const val = parseInt(this.value, 10);
      updateSliderPosition(val);
    });

    // Keyboard Arrow navigation
    document.addEventListener('keydown', function(e) {
      if (!slider || !capturesIndex || capturesIndex.length === 0) return;
      if (document.activeElement && (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA')) return;
      let val = parseInt(slider.value, 10);
      if (e.key === 'ArrowLeft') {
        if (val > 0) {
          slider.value = val - 1;
          updateSliderPosition(val - 1);
        }
      } else if (e.key === 'ArrowRight') {
        if (val < capturesIndex.length - 1) {
          slider.value = val + 1;
          updateSliderPosition(val + 1);
        }
      }
    });
  })();
  </script>

{% else %}
  <!-- Empty State Welcome Card -->
  <div class="bg-white rounded-xl border border-slate-200 p-8 text-center max-w-2xl mx-auto my-6 shadow-xs">
    <div class="w-16 h-16 bg-indigo-50 text-indigo-600 rounded-full flex items-center justify-center mx-auto mb-4 border border-indigo-100">
      <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path>
      </svg>
    </div>
    <h2 class="text-xl font-bold text-slate-900 mb-2">Welcome to OpenRecall</h2>
    <p class="text-sm text-slate-600 mb-6 max-w-lg mx-auto">
      OpenRecall is active and monitoring your desktop in the background. Visual snapshots will automatically appear here as screen changes occur.
    </p>

    <div class="bg-slate-50 rounded-lg p-4 border border-slate-200 text-left text-xs max-w-md mx-auto space-y-3">
      <div class="flex items-center justify-between pb-2 border-b border-slate-200">
        <span class="font-semibold text-slate-700">Capture Pipeline:</span>
        {% if is_paused %}
          <span class="px-2 py-0.5 rounded bg-amber-100 text-amber-800 font-semibold">Paused</span>
        {% else %}
          <span class="px-2 py-0.5 rounded bg-emerald-100 text-emerald-800 font-semibold">Recording Active</span>
        {% endif %}
      </div>
      <div class="flex items-center justify-between pb-2 border-b border-slate-200">
        <span class="font-semibold text-slate-700">Tesseract OCR Engine:</span>
        {% if ocr_available %}
          <span class="px-2 py-0.5 rounded bg-sky-100 text-sky-800 font-semibold">Available</span>
        {% else %}
          <span class="px-2 py-0.5 rounded bg-amber-100 text-amber-800 font-semibold">Unavailable</span>
        {% endif %}
      </div>
      <div class="flex items-center justify-between">
        <span class="font-semibold text-slate-700">Local Data Directory:</span>
        <code class="text-[11px] text-slate-600 truncate max-w-[200px]" title="{{ appdata_folder }}">{{ appdata_folder }}</code>
      </div>
    </div>
  </div>
{% endif %}
{% endblock %}
""",
        total_count=total_count,
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
<div class="flex flex-col sm:flex-row items-start sm:items-center justify-between pb-4 border-b border-slate-200 mb-6 gap-4">
  <div>
    <h1 class="text-xl font-bold text-slate-900">
      {% if q %}Search Results for &ldquo;{{ q }}&rdquo;{% else %}Search All Records{% endif %}
    </h1>
    <p class="text-xs text-slate-500 mt-0.5">Page {{ page }} &bull; {{ entries|length }} matching captures</p>
  </div>
</div>

<!-- Filter Bar -->
<form method="get" action="/search" class="bg-white rounded-xl border border-slate-200 p-4 shadow-xs mb-6">
  <input type="hidden" name="q" value="{{ q }}">
  <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3 items-end">
    <div>
      <label class="block text-xs font-semibold text-slate-700 mb-1">Application</label>
      <select class="w-full px-3 py-1.5 text-xs bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500" name="app">
        <option value="">All Applications</option>
        {% for a in available_apps %}
          <option value="{{ a }}" {% if request.args.get('app') == a %}selected{% endif %}>{{ a }}</option>
        {% endfor %}
      </select>
    </div>
    <div>
      <label class="block text-xs font-semibold text-slate-700 mb-1">From Date</label>
      <input type="date" class="w-full px-3 py-1.5 text-xs bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500" name="start_date" value="{{ request.args.get('start_date', '') }}">
    </div>
    <div>
      <label class="block text-xs font-semibold text-slate-700 mb-1">To Date</label>
      <input type="date" class="w-full px-3 py-1.5 text-xs bg-slate-50 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500" name="end_date" value="{{ request.args.get('end_date', '') }}">
    </div>
    <div class="flex items-center gap-2">
      <button type="submit" class="flex-1 px-4 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold rounded-lg transition-colors cursor-pointer">Filter</button>
      <a href="/search?q={{ q }}" class="px-3 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-semibold rounded-lg border border-slate-200 transition-colors">Reset</a>
    </div>
  </div>
</form>

{% if entries|length > 0 %}
  <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
    {% for entry in entries %}
      <div class="bg-white rounded-xl border border-slate-200 shadow-xs hover:shadow-md hover:border-slate-300 transition-all overflow-hidden flex flex-col group">
        <a href="/capture/{{ entry.id }}" class="block bg-slate-900 aspect-video overflow-hidden relative">
          <img src="/screenshot/{{ entry.image_path or (entry.timestamp|string + '_0.webp') }}" 
               loading="lazy"
               class="w-full h-full object-cover group-hover:scale-102 transition-transform duration-200" alt="Screenshot">
        </a>
        <div class="p-4 flex flex-col flex-1 justify-between gap-3">
          <div>
            <div class="flex items-center justify-between text-xs text-slate-500 mb-1">
              <span class="px-2 py-0.5 rounded bg-slate-100 text-slate-700 font-medium text-[11px] border border-slate-200 truncate max-w-[140px]">{{ entry.app or 'Unknown App' }}</span>
              {% if entry.monitor and entry.monitor > 1 %}
                <span class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 text-[10px] font-semibold border border-slate-200">Mon {{ entry.monitor }}</span>
              {% endif %}
              <span class="font-medium text-slate-500 ml-auto">{{ entry.timestamp | timestamp_to_human_readable }}</span>
            </div>
            <h3 class="font-bold text-slate-900 text-sm truncate mb-2 mt-1"><a href="/capture/{{ entry.id }}" class="hover:text-indigo-600 transition-colors" title="{{ entry.title }}">{{ entry.title or 'Untitled Window' }}</a></h3>
            {% if entry.text %}
              <p class="text-xs text-slate-600 line-clamp-3 leading-relaxed bg-slate-50 p-2.5 rounded-lg border border-slate-100 font-mono">{{ entry.text }}</p>
            {% endif %}
          </div>
        </div>
      </div>
    {% endfor %}
  </div>

  <!-- Pagination Controls -->
  <nav aria-label="Search pagination" class="mt-8 flex justify-center">
    <div class="inline-flex items-center gap-2">
      {% if page > 1 %}
        <a class="px-3 py-1.5 text-xs font-medium text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 transition-colors" href="/search?q={{ q }}&page={{ page - 1 }}&limit={{ limit }}{% if request.args.get('app') %}&app={{ request.args.get('app') }}{% endif %}{% if request.args.get('start_date') %}&start_date={{ request.args.get('start_date') }}{% endif %}{% if request.args.get('end_date') %}&end_date={{ request.args.get('end_date') }}{% endif %}">Previous</a>
      {% else %}
        <span class="px-3 py-1.5 text-xs font-medium text-slate-400 bg-slate-100 border border-slate-200 rounded-lg cursor-not-allowed">Previous</span>
      {% endif %}
      <span class="px-3 py-1.5 text-xs font-semibold text-indigo-600 bg-indigo-50 border border-indigo-200 rounded-lg">Page {{ page }}</span>
      {% if entries|length == limit %}
        <a class="px-3 py-1.5 text-xs font-medium text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 transition-colors" href="/search?q={{ q }}&page={{ page + 1 }}&limit={{ limit }}{% if request.args.get('app') %}&app={{ request.args.get('app') }}{% endif %}{% if request.args.get('start_date') %}&start_date={{ request.args.get('start_date') }}{% endif %}{% if request.args.get('end_date') %}&end_date={{ request.args.get('end_date') }}{% endif %}">Next</a>
      {% else %}
        <span class="px-3 py-1.5 text-xs font-medium text-slate-400 bg-slate-100 border border-slate-200 rounded-lg cursor-not-allowed">Next</span>
      {% endif %}
    </div>
  </nav>

{% else %}
  <div class="bg-white rounded-xl border border-slate-200 p-8 text-center max-w-md mx-auto my-8 shadow-xs">
    <div class="w-12 h-12 bg-amber-50 text-amber-500 rounded-full flex items-center justify-center mx-auto mb-3 border border-amber-100">
      <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
      </svg>
    </div>
    <h3 class="text-base font-bold text-slate-900 mb-1">No matching memory records</h3>
    <p class="text-xs text-slate-500 mb-0">No records match your search query &ldquo;{{ q }}&rdquo;.</p>
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
  <div class="bg-white rounded-xl border border-amber-200 p-8 text-center max-w-md mx-auto my-8 shadow-xs">
    <div class="w-12 h-12 bg-amber-50 text-amber-500 rounded-full flex items-center justify-center mx-auto mb-3 border border-amber-100">
      <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
      </svg>
    </div>
    <h3 class="text-base font-bold text-slate-900 mb-1">Capture Not Found</h3>
    <p class="text-xs text-slate-500 mb-4">No screenshot record exists for database ID {{ entry_id }}.</p>
    <a href="/" class="inline-flex items-center px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold rounded-lg transition-colors">&larr; Return to Timeline</a>
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
<div class="flex items-center justify-between mb-6 pb-4 border-b border-slate-200">
  <a href="/" class="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 transition-colors">
    &larr; Back to Timeline
  </a>
  <div class="inline-flex items-center gap-2">
    {% if entry.id > 1 %}
      <a href="/capture/{{ entry.id - 1 }}" class="px-3 py-1.5 text-xs font-semibold text-indigo-600 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 transition-colors">&larr; Previous</a>
    {% endif %}
    <a href="/capture/{{ entry.id + 1 }}" class="px-3 py-1.5 text-xs font-semibold text-indigo-600 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 transition-colors">Next &rarr;</a>
  </div>
</div>

<div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
  <!-- Screenshot Display -->
  <div class="lg:col-span-8">
    <div class="bg-slate-950 rounded-xl overflow-hidden p-2 shadow-inner border border-slate-900 flex items-center justify-center min-h-[400px]">
      <img src="/screenshot/{{ entry.image_path or (entry.timestamp|string + '_0.webp') }}" 
           class="max-h-[75vh] w-auto h-auto object-contain rounded mx-auto" alt="Full Resolution Screenshot">
    </div>
  </div>

  <!-- Metadata & Extracted Text -->
  <div class="lg:col-span-4 flex flex-col gap-6">
    <div class="bg-white rounded-xl border border-slate-200 p-5 shadow-xs">
      <div class="flex items-center justify-between pb-3 border-b border-slate-100 mb-4">
        <h3 class="text-sm font-bold text-slate-900">Capture Metadata</h3>
        {% if entry.monitor and entry.monitor > 1 %}
          <span class="px-2 py-0.5 rounded bg-slate-100 text-slate-600 text-[10px] font-semibold border border-slate-200">Monitor {{ entry.monitor }}</span>
        {% endif %}
      </div>
      
      <dl class="space-y-3 text-xs">
        <div>
          <dt class="text-slate-400 font-medium">Application</dt>
          <dd class="text-slate-900 font-semibold truncate mt-0.5" title="{{ entry.app }}">{{ entry.app or 'Unknown App' }}</dd>
        </div>
        <div>
          <dt class="text-slate-400 font-medium">Window Title</dt>
          <dd class="text-slate-700 truncate mt-0.5" title="{{ entry.title }}">{{ entry.title or 'Untitled Window' }}</dd>
        </div>
        <div>
          <dt class="text-slate-400 font-medium">Timestamp</dt>
          <dd class="text-slate-700 mt-0.5">{{ entry.timestamp | timestamp_to_human_readable }}</dd>
        </div>
        <div>
          <dt class="text-slate-400 font-medium">Database ID</dt>
          <dd class="text-slate-700 font-mono mt-0.5">#{{ entry.id }}</dd>
        </div>
        <div>
          <dt class="text-slate-400 font-medium">File Path</dt>
          <dd class="text-slate-600 font-mono text-[11px] truncate mt-0.5" title="{{ entry.image_path }}">{{ entry.image_path }}</dd>
        </div>
      </dl>

      <hr class="my-4 border-slate-100">

      <div class="flex items-center justify-between mb-2">
        <span class="text-xs font-bold text-slate-700 uppercase tracking-wider">Extracted OCR Text</span>
        {% if entry.text %}
          <button class="px-2 py-1 text-[11px] font-semibold bg-indigo-50 text-indigo-700 hover:bg-indigo-100 rounded border border-indigo-200 transition-colors cursor-pointer" onclick="copyOcrText()">Copy Text</button>
        {% endif %}
      </div>
      {% if entry.text %}
        <pre id="ocrTextBlock" class="bg-slate-50 p-3 rounded-lg border border-slate-200 text-xs text-slate-700 font-mono max-h-60 overflow-y-auto whitespace-pre-wrap break-words leading-relaxed">{{ entry.text }}</pre>
      {% else %}
        <p class="text-xs text-slate-400 italic">No OCR text extracted for this capture.</p>
      {% endif %}
    </div>
  </div>
</div>

<script>
function copyOcrText() {
  const el = document.getElementById('ocrTextBlock');
  if (!el) return;
  const text = el.innerText;
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


@app.route("/api/timeline/index")
def api_timeline_index():
    """REST API endpoint returning discrete captures index array for timeline navigation."""
    items = get_timeline_captures_index()
    return jsonify({
        "count": len(items),
        "captures": items,
    })


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
    """Serves WebP screenshot files safely from normalized absolute active and historical appdata locations."""
    safe_filename = os.path.basename(filename)
    if not safe_filename or safe_filename != filename or safe_filename.startswith("."):
        return jsonify({"error": "Invalid screenshot filename"}), 400

    import openrecall.config as config
    active_dir = os.path.abspath(config.screenshots_path)
    active_file = os.path.join(active_dir, safe_filename)
    if os.path.exists(active_file):
        return send_from_directory(active_dir, safe_filename)

    try:
        from openrecall.config import get_appdata_folder
        default_folder = os.path.abspath(get_appdata_folder())
        default_screenshots = os.path.abspath(os.path.join(default_folder, "screenshots"))
        if os.path.normpath(default_screenshots) != os.path.normpath(active_dir):
            hist_file = os.path.join(default_screenshots, safe_filename)
            if os.path.exists(hist_file):
                return send_from_directory(default_screenshots, safe_filename)
    except Exception:
        pass

    return jsonify({"error": "Screenshot file not found"}), 404


@app.route("/static/<path:filename>")
def serve_static(filename):
    """Serves compiled static CSS/JS assets or fallback legacy screenshots."""
    if filename.endswith(".webp"):
        return serve_image(os.path.basename(filename))
    static_dir = os.path.join(app.root_path, "static")
    if os.path.exists(os.path.join(static_dir, filename)):
        return send_from_directory(static_dir, filename)
    return send_from_directory(screenshots_path, filename)


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
