"""Flask web application and REST API for OpenRecall timeline and search UX."""

import html
import os
import re
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
    delete_entry_by_id,
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
from openrecall.platform import get_platform_provider
from openrecall.privacy import get_privacy_policy
from openrecall.screenshot import get_capture_pipeline, record_screenshots_thread
from openrecall.utils import (
    SingleInstanceLock,
    check_existing_instance_running,
    human_readable_time,
    timestamp_to_human_readable,
)

app = Flask(__name__)


def highlight_search_matches(text: Optional[str], query: Optional[str]) -> str:
    """Highlights occurrences of query terms in OCR text with <mark> tags safely."""
    if not text:
        return ""
    escaped_text = html.escape(text)
    if not query or not query.strip():
        return escaped_text

    words = [re.escape(w) for w in query.strip().split() if w.strip()]
    if not words:
        return escaped_text

    pattern = re.compile(r"(" + "|".join(words) + r")", re.IGNORECASE)
    return pattern.sub(
        r'<mark class="bg-amber-200 text-amber-950 rounded px-1 font-semibold">\1</mark>',
        escaped_text,
    )


app.jinja_env.filters["human_readable_time"] = human_readable_time
app.jinja_env.filters["timestamp_to_human_readable"] = timestamp_to_human_readable
app.jinja_env.filters["highlight_search_matches"] = highlight_search_matches


@app.context_processor
def inject_global_template_context():
    policy = get_privacy_policy()
    ocr_provider = TesseractOCRProvider()
    platform_provider = get_platform_provider()
    css_path = os.path.join(os.path.dirname(__file__), "static", "css", "output.css")
    css_v = int(os.path.getmtime(css_path)) if os.path.exists(css_path) else 1
    return {
        "is_paused": policy.is_paused(),
        "ocr_available": ocr_provider.is_available(),
        "autostart_enabled": platform_provider.is_startup_enabled(),
        "appdata_folder": appdata_folder,
        "css_version": css_v,
    }


base_template = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>OpenRecall - Digital Memory</title>
  <link rel="stylesheet" href="/static/css/output.css?v={{ css_version }}">
  <style>
    /* Baseline layout fallbacks and keyboard focus enhancements */
    *, *::before, *::after { box-sizing: border-box; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background-color: #f8fafc; color: #0f172a; line-height: 1.5; }
    
    a:focus-visible, button:focus-visible, input:focus-visible {
      outline: 2px solid #4f46e5;
      outline-offset: 2px;
    }
    
    .timeline-slider { -webkit-appearance: none; appearance: none; width: 100%; height: 8px; border-radius: 9999px; background: #cbd5e1; outline: none; transition: background 0.15s ease-in-out; }
    .timeline-slider:hover { background: #94a3b8; }
    .timeline-slider::-webkit-slider-thumb { -webkit-appearance: none; appearance: none; width: 20px; height: 20px; border-radius: 50%; background: #4f46e5; border: 2px solid #ffffff; box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.15); cursor: pointer; transition: transform 0.1s ease, background-color 0.15s ease; }
    .timeline-slider::-webkit-slider-thumb:hover { background: #4338ca; transform: scale(1.15); }
    .timeline-slider::-moz-range-thumb { width: 20px; height: 20px; border-radius: 50%; background: #4f46e5; border: 2px solid #ffffff; box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.15); cursor: pointer; transition: transform 0.1s ease, background-color 0.15s ease; }
    .timeline-slider::-moz-range-thumb:hover { background: #4338ca; transform: scale(1.15); }
  </style>
</head>
<body class="bg-slate-50 text-slate-800 flex flex-col min-h-screen">
  <!-- Header Navigation -->
  <header class="bg-white border-b border-slate-200 sticky top-0 z-30 shadow-xs">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
      <div class="flex items-center justify-between h-16 gap-4">
        
        <!-- Brand Title -->
        <div class="flex items-center gap-3 shrink-0">
          <a href="/" class="flex items-center gap-2 text-slate-900 font-bold text-lg hover:text-indigo-600 transition-colors">
            <svg class="w-6 h-6 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path>
            </svg>
            <span>OpenRecall</span>
          </a>
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
                   type="search" name="q" value="{{ request.args.get('q', '') }}" placeholder="Search local memory..." aria-label="Search local memory">
            <button class="absolute inset-y-1 right-1 px-3 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold rounded-md transition-colors cursor-pointer" type="submit">
              Search
            </button>
          </div>
        </form>

      </div>
    </div>
  </header>

  <!-- Dedicated Capture Control & Status Sub-bar -->
  <div class="bg-slate-100/70 border-b border-slate-200/80 py-2">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex items-center justify-between gap-4 text-xs">
      <div class="flex items-center gap-2 font-medium">
        <span class="text-slate-500">Capture Status:</span>
        {% if is_paused %}
          <span class="inline-flex items-center gap-1.5 font-semibold text-amber-700 bg-amber-50 px-2 py-0.5 rounded-md border border-amber-200/80" title="Capturing is paused">
            <span class="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse"></span>
            <span>Capture Paused</span>
          </span>
        {% else %}
          <span class="inline-flex items-center gap-1.5 font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-md border border-emerald-200/80" title="Capturing is active">
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
            <span>Capture Active</span>
          </span>
        {% endif %}
      </div>
      <div>
        {% if is_paused %}
          <form action="/api/resume" method="post" class="inline m-0">
            <button type="submit" class="inline-flex items-center px-2.5 py-1 rounded text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 text-white transition-colors cursor-pointer shadow-2xs">
              Resume Capture
            </button>
          </form>
        {% else %}
          <form action="/api/pause" method="post" class="inline m-0">
            <button type="submit" class="inline-flex items-center px-2.5 py-1 rounded text-xs font-semibold bg-white hover:bg-slate-50 text-slate-700 border border-slate-300 transition-colors cursor-pointer shadow-2xs">
              Pause Capture
            </button>
          </form>
        {% endif %}
      </div>
    </div>
  </div>

  <!-- Main Content Container -->
  <main class="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6">
    {% block content %}{% endblock %}
  </main>

  <!-- Footer with Autostart & Open-Source Project Links -->
  <footer class="bg-white border-t border-slate-200 py-4 mt-auto">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col sm:flex-row items-center justify-between gap-3 text-xs text-slate-500">
      <div class="flex items-center gap-3 flex-wrap">
        <span>OpenRecall &bull; Open-source digital memory assistant</span>
        <span class="text-slate-300 hidden sm:inline">|</span>
        <div class="inline-flex items-center gap-1.5" title="System login autostart status">
          {% if autostart_enabled %}
            <span class="w-2 h-2 rounded-full bg-emerald-500"></span>
            <span class="font-medium text-slate-700">Autostart: Active</span>
          {% else %}
            <span class="w-2 h-2 rounded-full bg-slate-300"></span>
            <span class="font-medium text-slate-500">Autostart: Inactive</span>
          {% endif %}
          <button type="button" onclick="toggleAutostartInfo()" class="px-1.5 py-0.5 text-[11px] font-semibold text-indigo-600 bg-indigo-50 border border-indigo-200 hover:bg-indigo-100 rounded transition-colors cursor-pointer" aria-label="Autostart help information">
            Help
          </button>
        </div>
      </div>
      <div class="flex items-center gap-4">
        <a href="https://github.com/Shiva9168/openrecall" target="_blank" rel="noopener noreferrer" 
           class="hover:text-indigo-600 transition-colors inline-flex items-center gap-1.5 font-medium text-slate-600">
          <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24">
            <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/>
          </svg>
          <span>View on GitHub</span>
        </a>
      </div>
    </div>
  </footer>

  <!-- Centered Autostart Help Modal -->
  <div id="autostartInfoModal" class="hidden fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/40 backdrop-blur-xs transition-opacity" aria-modal="true" role="dialog">
    <div class="fixed inset-0" onclick="toggleAutostartInfo()"></div>
    <div class="relative bg-white border border-slate-200/90 rounded-xl shadow-xl max-w-md w-full p-5 space-y-4 text-xs text-slate-600 z-10">
      <div class="flex items-center justify-between border-b border-slate-100 pb-3">
        <h3 class="font-bold text-slate-900 text-sm">Autostart</h3>
        <button type="button" onclick="toggleAutostartInfo()" class="text-slate-400 hover:text-slate-600 font-medium text-base leading-none cursor-pointer p-1 rounded hover:bg-slate-100 transition-colors" aria-label="Close modal">&times;</button>
      </div>

      <div class="space-y-3">
        <p class="text-slate-600 leading-relaxed text-xs">
          Start OpenRecall automatically when you sign in to your computer.
        </p>

        <div class="flex items-center justify-between bg-slate-50 px-3 py-2 rounded-lg border border-slate-200/70 text-xs">
          <span class="font-medium text-slate-700">Status</span>
          {% if autostart_enabled %}
            <span class="inline-flex items-center gap-1.5 font-semibold text-emerald-700">
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
              <span>Enabled</span>
            </span>
          {% else %}
            <span class="inline-flex items-center gap-1.5 font-medium text-slate-500">
              <span class="w-1.5 h-1.5 rounded-full bg-slate-400"></span>
              <span>Disabled</span>
            </span>
          {% endif %}
        </div>

        <div class="bg-slate-900 text-slate-200 rounded-lg p-3 space-y-2.5 font-mono text-[11px] border border-slate-800">
          <div>
            <div class="font-sans text-[11px] text-slate-400 font-medium mb-1">Enable</div>
            <code class="text-indigo-300 bg-slate-800/80 px-2 py-1 rounded block select-all">openrecall --enable-autostart</code>
          </div>
          <div>
            <div class="font-sans text-[11px] text-slate-400 font-medium mb-1">Disable</div>
            <code class="text-slate-300 bg-slate-800/80 px-2 py-1 rounded block select-all">openrecall --disable-autostart</code>
          </div>
        </div>
      </div>

      <div class="pt-2 flex justify-end border-t border-slate-100">
        <button type="button" onclick="toggleAutostartInfo()" class="px-3.5 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-semibold rounded-md transition-colors cursor-pointer">
          Close
        </button>
      </div>
    </div>
  </div>
  <script>
  function toggleAutostartInfo() {
    const modal = document.getElementById('autostartInfoModal');
    if (modal) modal.classList.toggle('hidden');
  }
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      const modal = document.getElementById('autostartInfoModal');
      if (modal && !modal.classList.contains('hidden')) {
        modal.classList.add('hidden');
      }
    }
  });
  </script>
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
    import openrecall.config as config
    from openrecall.ocr import get_ocr_provider, OCR_FAILED_SENTINEL

    text_val = entry.text or ""
    ocr_available = get_ocr_provider().is_available()

    if not ocr_available:
        ocr_status = "unavailable"
        clean_text = ""
    elif text_val == OCR_FAILED_SENTINEL:
        ocr_status = "failed"
        clean_text = ""
    elif text_val.strip():
        ocr_status = "success"
        clean_text = text_val.strip()
    else:
        ocr_status = "empty"
        clean_text = ""

    snippet = clean_text[:200] + ("..." if len(clean_text) > 200 else "")
    img_name = entry.image_path or (f"{entry.timestamp}_0.webp" if entry.timestamp else "Unavailable")

    file_exists = False
    if entry.image_path:
        if os.path.isabs(entry.image_path):
            abs_p = os.path.normpath(entry.image_path)
        else:
            abs_p = os.path.normpath(os.path.join(config.screenshots_path, entry.image_path))
        file_exists = os.path.exists(abs_p)

    return {
        "id": entry.id,
        "timestamp": entry.timestamp,
        "human_time": timestamp_to_human_readable(entry.timestamp),
        "app": entry.app or "Unknown App",
        "title": entry.title or "Unknown Title",
        "image_path": img_name,
        "image_url": f"/screenshot/{img_name}",
        "file_exists": file_exists,
        "is_deleted": getattr(entry, "is_deleted", 0),
        "text_snippet": snippet,
        "text": clean_text,
        "raw_text": text_val,
        "ocr_status": ocr_status,
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
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")

        start_ts = _parse_date_to_timestamp(start_date, end_of_day=False)
        end_ts = _parse_date_to_timestamp(end_date, end_of_day=True)

        entries = get_timeline_entries(
            start_time=start_ts,
            end_time=end_ts,
            limit=limit,
            offset=offset,
        )

        entries_dicts = [_entry_to_dict(e) for e in entries]

        return render_template_string(
            """
{% extends "base_template" %}
{% block content %}
<div class="flex flex-col sm:flex-row items-start sm:items-center justify-between pb-4 border-b border-slate-200 mb-6 gap-4">
  <div>
    <h1 class="text-xl font-bold text-slate-900">Digital Memory Gallery</h1>
    <p class="text-xs text-slate-500 mt-0.5">Visual grid of screen capture moments</p>
  </div>
  <div class="inline-flex rounded-lg p-1 bg-slate-200/70 border border-slate-200">
    <a href="/?mode=timeline" class="px-3 py-1.5 text-xs font-semibold rounded-md text-slate-700 hover:text-slate-900 transition-colors">Timeline View</a>
    <a href="/?mode=gallery" class="px-3 py-1.5 text-xs font-semibold rounded-md bg-white text-indigo-600 shadow-xs">Gallery Grid</a>
  </div>
</div>

{% if entries|length > 0 %}
  <!-- 5 Column Dense Desktop Grid -->
  <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
    {% for entry in entries %}
      <div class="bg-white rounded-xl border border-slate-200 shadow-xs hover:shadow-md hover:border-slate-300 transition-all overflow-hidden flex flex-col group">
        <a href="/capture/{{ entry.id }}" class="block bg-slate-950 aspect-video overflow-hidden relative">
          {% if entry.file_exists %}
            <img src="/screenshot/{{ entry.image_path or (entry.timestamp|string + '_0.webp') }}" 
                 loading="lazy" decoding="async"
                 class="w-full h-full object-cover group-hover:scale-102 transition-transform duration-200" alt="Screenshot"
                 onerror="this.onerror=null; this.parentElement.innerHTML='<div class=\\'w-full h-full flex flex-col items-center justify-center bg-slate-900 text-amber-400 p-2 text-center\\'><svg class=\\'w-6 h-6 mb-1 text-amber-500\\' fill=\\'none\\' stroke=\\'currentColor\\' viewBox=\\'0 0 24 24\\'><path stroke-linecap=\\'round\\' stroke-linejoin=\\'round\\' stroke-width=\\'2\\' d=\\'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z\\'></path></svg><span class=\\'text-[10px] font-mono font-bold\\'>MISSING_FILE</span></div>';">
          {% else %}
            <div class="w-full h-full flex flex-col items-center justify-center bg-slate-900 text-amber-400 p-2 text-center">
              <svg class="w-6 h-6 mb-1 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
              </svg>
              <span class="text-[10px] font-mono font-bold">MISSING_FILE</span>
            </div>
          {% endif %}
        </a>
        <div class="p-2.5 flex items-center justify-between bg-white border-t border-slate-100 text-xs">
          <span class="font-medium text-slate-700 text-[11px] truncate">{{ entry.timestamp | timestamp_to_human_readable }}</span>
          <a href="/capture/{{ entry.id }}" class="text-[11px] font-semibold text-indigo-600 hover:text-indigo-800 transition-colors shrink-0">
            Inspect &rarr;
          </a>
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
    <p class="text-xs text-slate-500 mb-0">No desktop screen captures available in gallery.</p>
  </div>
{% endif %}
{% endblock %}
""",
            entries=entries_dicts,
            page=page,
            limit=limit,
            total_count=total_count,
        )

    # Default: Timeline mode with Discrete Range Slider
    latest_ts = bounds.get("latest_ts")
    latest_entry = get_entry_nearest_timestamp(latest_ts) if latest_ts is not None else None
    latest_entry_dict = _entry_to_dict(latest_entry) if latest_entry else None

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
      <div class="flex items-center gap-2 flex-wrap">
        <svg class="w-5 h-5 text-indigo-600 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path>
        </svg>
        <h2 id="timelineTimeLabel" class="text-base font-bold text-slate-900">
          {{ latest_entry.timestamp | timestamp_to_human_readable }}
        </h2>
        <span id="timelineTimeGap" class="hidden text-xs text-amber-700 bg-amber-50 border border-amber-200 px-2 py-0.5 rounded-full font-medium"></span>
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
          <span>&larr;</span> <span>Use Arrow keys to step frame-by-frame</span> <span>&rarr;</span>
        </span>
        <span>Latest Capture</span>
      </div>
    </div>

    <!-- Single Screenshot Image Display Frame -->
    <div id="timelineImgContainer" class="relative bg-slate-950 rounded-xl overflow-hidden p-2 shadow-inner border border-slate-900 flex items-center justify-center min-h-[300px]">
      <a id="timelineImgLink" href="/capture/{{ latest_entry.id }}" class="block max-w-full" title="Click for capture details">
        <img id="timelineImg" src="/screenshot/{{ latest_entry.image_path or (latest_entry.timestamp|string + '_0.webp') }}"
             class="max-h-[65vh] w-auto h-auto object-contain rounded transition-opacity duration-150 mx-auto {% if latest_entry_dict and not latest_entry_dict.file_exists %}hidden{% endif %}" alt="Timeline Capture"
             onerror="handleTimelineImgError(this);">
      </a>
      <div id="timelineImgFallback" class="{% if latest_entry_dict and latest_entry_dict.file_exists %}hidden{% endif %} text-center p-8">
        <div class="w-12 h-12 bg-amber-950/50 text-amber-500 rounded-full flex items-center justify-center mx-auto mb-3 border border-amber-800/50">
          <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
          </svg>
        </div>
        <p class="text-xs font-semibold text-amber-400 mb-1">Screenshot file is missing from disk</p>
        <p class="text-[11px] text-slate-400">Memory record and extracted text remain available.</p>
      </div>
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
  function handleTimelineImgError(imgEl) {
    if (!imgEl) return;
    imgEl.classList.add('hidden');
    const fallback = document.getElementById('timelineImgFallback');
    if (fallback) fallback.classList.remove('hidden');
  }

  (function() {
    const slider = document.getElementById('timelineSlider');
    const timeLabel = document.getElementById('timelineTimeLabel');
    const timeGapLabel = document.getElementById('timelineTimeGap');
    const counterLabel = document.getElementById('timelineCounter');
    const img = document.getElementById('timelineImg');
    const imgLink = document.getElementById('timelineImgLink');
    const imgFallback = document.getElementById('timelineImgFallback');
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
      if (img) {
        img.classList.remove('hidden');
        img.src = entry.image_url || ('/screenshot/' + entry.image_path);
      }
      if (imgFallback) imgFallback.classList.add('hidden');
      if (!entry.file_exists && imgFallback && img) {
        img.classList.add('hidden');
        imgFallback.classList.remove('hidden');
      }
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
          if (reqId !== activeReqId) return;
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

        if (boundedIdx > 0 && capturesIndex[boundedIdx - 1]) {
          const prevTs = capturesIndex[boundedIdx - 1].timestamp;
          const currTs = item.timestamp;
          const gapSec = currTs - prevTs;
          if (gapSec > 300 && timeGapLabel) {
            const mins = Math.floor(gapSec / 60);
            const hrs = Math.floor(mins / 60);
            let gapText = '';
            if (hrs > 0) {
              gapText = hrs + 'h ' + (mins % 60) + 'm gap';
            } else {
              gapText = mins + 'm gap';
            }
            timeGapLabel.innerText = '⏱️ (' + gapText + ' since previous capture)';
            timeGapLabel.classList.remove('hidden');
          } else if (timeGapLabel) {
            timeGapLabel.classList.add('hidden');
          }
        } else if (timeGapLabel) {
          timeGapLabel.classList.add('hidden');
        }
      }
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function() {
        fetchCaptureForIndex(boundedIdx);
      }, 80);
    }

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
      OpenRecall runs locally in the background to capture memory snapshots of your screen. As screen activity occurs, memory moments will appear in your timeline.
    </p>

    <div class="bg-slate-50 rounded-lg p-4 border border-slate-200 text-left text-xs max-w-md mx-auto space-y-3">
      <div class="flex items-center justify-between pb-2 border-b border-slate-200">
        <span class="font-semibold text-slate-700">Capture Pipeline:</span>
        {% if is_paused %}
          <span class="px-2 py-0.5 rounded bg-amber-100 text-amber-800 font-semibold">Capture Paused</span>
        {% else %}
          <span class="px-2 py-0.5 rounded bg-emerald-100 text-emerald-800 font-semibold">Capture Active</span>
        {% endif %}
      </div>
      <div class="flex items-center justify-between pb-2 border-b border-slate-200">
        <span class="font-semibold text-slate-700">Tesseract OCR Engine:</span>
        {% if ocr_available %}
          <span class="px-2 py-0.5 rounded bg-sky-100 text-sky-800 font-semibold">Available</span>
        {% else %}
          <span class="px-2 py-0.5 rounded bg-amber-100 text-amber-800 font-semibold" title="Screenshots captured without text search">Unavailable (Capture still active)</span>
        {% endif %}
      </div>
      <div class="flex items-center justify-between pb-2 border-b border-slate-200">
        <span class="font-semibold text-slate-700">System Autostart:</span>
        {% if autostart_enabled %}
          <span class="px-2 py-0.5 rounded bg-emerald-100 text-emerald-800 font-semibold">Enabled</span>
        {% else %}
          <span class="px-2 py-0.5 rounded bg-slate-200 text-slate-700 font-semibold">Disabled</span>
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
        latest_entry_dict=latest_entry_dict,
    )


@app.route("/search")
def search():
    """Renders the paginated search results view with date filter controls."""
    q = request.args.get("q", "")
    page, limit, offset = _get_pagination_params()
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    start_ts = _parse_date_to_timestamp(start_date, end_of_day=False)
    end_ts = _parse_date_to_timestamp(end_date, end_of_day=True)

    matching_entries = search_entries(
        query=q,
        start_time=start_ts,
        end_time=end_ts,
        limit=limit,
        offset=offset,
    )

    matching_dicts = [_entry_to_dict(e) for e in matching_entries]

    return render_template_string(
        """
{% extends "base_template" %}
{% block content %}
<div class="flex flex-col sm:flex-row items-start sm:items-center justify-between pb-4 border-b border-slate-200 mb-6 gap-4">
  <div>
    <h1 class="text-xl font-bold text-slate-900">
      {% if q %}Search Results for &ldquo;{{ q }}&rdquo;{% else %}Search Memory Records{% endif %}
    </h1>
    <p class="text-xs text-slate-500 mt-0.5">Page {{ page }} &bull; {{ entries|length }} matching captures</p>
  </div>
</div>

<!-- Date Filter Bar -->
<form method="get" action="/search" class="bg-white rounded-xl border border-slate-200 p-4 shadow-xs mb-6">
  <input type="hidden" name="q" value="{{ q }}">
  <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end">
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
        <a href="/capture/{{ entry.id }}" class="block bg-slate-950 aspect-video overflow-hidden relative">
          {% if entry.file_exists %}
            <img src="/screenshot/{{ entry.image_path or (entry.timestamp|string + '_0.webp') }}" 
                 loading="lazy" decoding="async"
                 class="w-full h-full object-cover group-hover:scale-102 transition-transform duration-200" alt="Screenshot"
                 onerror="this.onerror=null; this.parentElement.innerHTML='<div class=\\'w-full h-full flex flex-col items-center justify-center bg-slate-900 text-amber-400 p-2 text-center\\'><svg class=\\'w-6 h-6 mb-1 text-amber-500\\' fill=\\'none\\' stroke=\\'currentColor\\' viewBox=\\'0 0 24 24\\'><path stroke-linecap=\\'round\\' stroke-linejoin=\\'round\\' stroke-width=\\'2\\' d=\\'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z\\'></path></svg><span class=\\'text-[10px] font-mono font-bold\\'>MISSING_FILE</span></div>';">
          {% else %}
            <div class="w-full h-full flex flex-col items-center justify-center bg-slate-900 text-amber-400 p-2 text-center">
              <svg class="w-6 h-6 mb-1 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
              </svg>
              <span class="text-[10px] font-mono font-bold">MISSING_FILE</span>
            </div>
          {% endif %}
        </a>
        <div class="p-4 flex flex-col flex-1 justify-between gap-3">
          <div>
            <div class="flex items-center justify-between text-xs text-slate-500 mb-2">
              <span class="font-medium text-slate-700">{{ entry.timestamp | timestamp_to_human_readable }}</span>
              {% if entry.monitor and entry.monitor > 1 %}
                <span class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 text-[10px] font-semibold border border-slate-200">Mon {{ entry.monitor }}</span>
              {% endif %}
            </div>
            {% if entry.text %}
              <p class="text-xs text-slate-700 line-clamp-3 leading-relaxed bg-slate-50 p-2.5 rounded-lg border border-slate-100 font-mono">
                {{ entry.text | highlight_search_matches(q) | safe }}
              </p>
            {% endif %}
          </div>
          <div class="pt-2 border-t border-slate-100 flex items-center justify-between">
            <span class="text-[11px] text-slate-400 font-mono">#{{ entry.id }}</span>
            <a href="/capture/{{ entry.id }}" class="text-xs font-semibold text-indigo-600 hover:text-indigo-800 transition-colors">
              Inspect &rarr;
            </a>
          </div>
        </div>
      </div>
    {% endfor %}
  </div>

  <!-- Pagination Controls -->
  <nav aria-label="Search pagination" class="mt-8 flex justify-center">
    <div class="inline-flex items-center gap-2">
      {% if page > 1 %}
        <a class="px-3 py-1.5 text-xs font-medium text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 transition-colors" href="/search?q={{ q }}&page={{ page - 1 }}&limit={{ limit }}{% if request.args.get('start_date') %}&start_date={{ request.args.get('start_date') }}{% endif %}{% if request.args.get('end_date') %}&end_date={{ request.args.get('end_date') }}{% endif %}">Previous</a>
      {% else %}
        <span class="px-3 py-1.5 text-xs font-medium text-slate-400 bg-slate-100 border border-slate-200 rounded-lg cursor-not-allowed">Previous</span>
      {% endif %}
      <span class="px-3 py-1.5 text-xs font-semibold text-indigo-600 bg-indigo-50 border border-indigo-200 rounded-lg">Page {{ page }}</span>
      {% if entries|length == limit %}
        <a class="px-3 py-1.5 text-xs font-medium text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 transition-colors" href="/search?q={{ q }}&page={{ page + 1 }}&limit={{ limit }}{% if request.args.get('start_date') %}&start_date={{ request.args.get('start_date') }}{% endif %}{% if request.args.get('end_date') %}&end_date={{ request.args.get('end_date') }}{% endif %}">Next</a>
      {% else %}
        <span class="px-3 py-1.5 text-xs font-medium text-slate-400 bg-slate-100 border border-slate-200 rounded-lg cursor-not-allowed">Next</span>
      {% endif %}
    </div>
  </nav>

{% else %}
  <div class="bg-white rounded-xl border border-slate-200 p-8 text-center max-w-md mx-auto my-8 shadow-xs">
    <div class="w-12 h-12 bg-slate-100 text-slate-400 rounded-full flex items-center justify-center mx-auto mb-3">
      <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
      </svg>
    </div>
    <h3 class="text-base font-bold text-slate-900 mb-1">No matching memory records</h3>
    <p class="text-xs text-slate-500 mb-4">No captures match search query &ldquo;{{ q }}&rdquo;.</p>
    <div class="text-left text-xs text-slate-600 bg-slate-50 border border-slate-200 rounded-lg p-3 space-y-1">
      <p class="font-semibold text-slate-700">Suggestions:</p>
      <ul class="list-disc list-inside space-y-0.5 text-[11px] text-slate-500">
        <li>Try broader search terms or partial words</li>
        <li>Check or clear date filters</li>
        <li>Verify Tesseract OCR is installed to extract text from screenshots</li>
      </ul>
    </div>
  </div>
{% endif %}
{% endblock %}
""",
        entries=matching_dicts,
        q=q,
        page=page,
        limit=limit,
    )


@app.route("/capture/<int:entry_id>")
def capture_detail(entry_id: int):
    """Renders the detailed single-screenshot inspection page with OCR text panel, path info, and dynamic prev/next navigation."""
    from openrecall.database import (
        get_entry_by_id,
        get_previous_capture_id,
        get_next_capture_id,
    )

    entry = get_entry_by_id(entry_id)

    # 1. Nonexistent ID -> 404
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

    # 2. Soft-deleted ID -> 410
    if getattr(entry, "is_deleted", 0) == 1:
        return (
            render_template_string(
                """
{% extends "base_template" %}
{% block content %}
  <div class="bg-white rounded-xl border border-rose-200 p-8 text-center max-w-md mx-auto my-8 shadow-xs">
    <div class="w-12 h-12 bg-rose-50 text-rose-500 rounded-full flex items-center justify-center mx-auto mb-3 border border-rose-100">
      <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path>
      </svg>
    </div>
    <h3 class="text-base font-bold text-slate-900 mb-1">Capture Deleted</h3>
    <p class="text-xs text-slate-500 mb-4">This screen capture has been permanently deleted and is unavailable.</p>
    <a href="/" class="inline-flex items-center px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold rounded-lg transition-colors">&larr; Return to Timeline</a>
  </div>
{% endblock %}
""",
                entry_id=entry_id,
            ),
            410,
        )

    # 3. Resolve image existence and stored paths
    import openrecall.config as config
    file_exists = False
    if entry.image_path:
        if os.path.isabs(entry.image_path):
            abs_p = os.path.normpath(entry.image_path)
            file_path_display = entry.image_path
        else:
            abs_p = os.path.normpath(os.path.join(config.screenshots_path, entry.image_path))
            file_path_display = abs_p
        file_exists = os.path.exists(abs_p)
        file_name_display = os.path.basename(entry.image_path)
    else:
        file_name_display = "Unavailable"
        file_path_display = "Unavailable"

    prev_id = get_previous_capture_id(entry.timestamp)
    next_id = get_next_capture_id(entry.timestamp)

    return render_template_string(
        """
{% extends "base_template" %}
{% block content %}
<div class="flex flex-wrap items-center justify-between mb-6 pb-4 border-b border-slate-200 gap-3">
  <a href="/" class="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 transition-colors">
    &larr; Back to Timeline
  </a>
  
  <div class="inline-flex items-center gap-2">
    {% if prev_id %}
      <a href="/capture/{{ prev_id }}" class="px-3 py-1.5 text-xs font-semibold text-indigo-600 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 transition-colors">&larr; Previous</a>
    {% else %}
      <span class="px-3 py-1.5 text-xs font-semibold text-slate-400 bg-slate-100 border border-slate-200 rounded-lg cursor-not-allowed">&larr; Previous</span>
    {% endif %}

    {% if next_id %}
      <a href="/capture/{{ next_id }}" class="px-3 py-1.5 text-xs font-semibold text-indigo-600 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 transition-colors">Next &rarr;</a>
    {% else %}
      <span class="px-3 py-1.5 text-xs font-semibold text-slate-400 bg-slate-100 border border-slate-200 rounded-lg cursor-not-allowed">Next &rarr;</span>
    {% endif %}

    <!-- Restrained Delete Action Button -->
    <button onclick="toggleDeleteConfirm()" type="button" class="px-3 py-1.5 text-xs font-semibold text-rose-700 bg-rose-50 hover:bg-rose-100 border border-rose-200 rounded-lg transition-colors cursor-pointer ml-2">
      Delete capture
    </button>
  </div>
</div>

<!-- Inline Delete Confirmation Box -->
<div id="deleteConfirmCard" class="hidden bg-rose-50 border border-rose-200 rounded-xl p-4 mb-6 text-xs text-rose-900 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
  <div>
    <h4 class="font-bold text-slate-900 text-sm mb-0.5">Delete this capture?</h4>
    <p class="text-slate-600">This removes the screenshot file and soft-deletes its memory record from OpenRecall views.</p>
  </div>
  <div class="flex items-center gap-2 shrink-0">
    <button onclick="toggleDeleteConfirm()" type="button" class="px-3 py-1.5 font-semibold text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 transition-colors cursor-pointer">Cancel</button>
    <form action="/api/capture/{{ entry.id }}/delete" method="post" class="inline m-0">
      <button type="submit" class="px-3 py-1.5 font-semibold text-white bg-rose-600 hover:bg-rose-700 rounded-lg transition-colors cursor-pointer">Confirm Delete</button>
    </form>
  </div>
</div>

<div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
  <!-- Screenshot Display or Missing File Card -->
  <div class="lg:col-span-8 flex flex-col gap-2">
    {% if file_exists %}
      <div class="flex items-center justify-between">
        <span class="text-xs font-semibold text-slate-500">Screenshot Preview</span>
        <button id="toggleZoomBtn" type="button" onclick="toggleImageScale()" class="text-xs font-semibold text-indigo-600 hover:text-indigo-800 bg-white border border-slate-300 rounded px-2.5 py-1 transition-colors cursor-pointer">
          Toggle 1:1 Scale
        </button>
      </div>
      <div id="imgContainer" class="bg-slate-950 rounded-xl overflow-auto p-2 shadow-inner border border-slate-900 flex items-center justify-center min-h-[400px]">
        <img id="detailImg" src="/screenshot/{{ entry.image_path or (entry.timestamp|string + '_0.webp') }}"
             class="max-h-[75vh] w-auto h-auto object-contain rounded mx-auto transition-all duration-150" alt="Full Resolution Screenshot">
      </div>
    {% else %}
      <div class="bg-slate-900 rounded-xl overflow-hidden p-8 shadow-inner border border-slate-800 flex flex-col items-center justify-center min-h-[400px] text-center">
        <div class="w-16 h-16 bg-amber-950/50 text-amber-500 rounded-full flex items-center justify-center mb-4 border border-amber-800/50">
          <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
          </svg>
        </div>
        <h3 class="text-lg font-bold text-amber-400 mb-2">Screenshot file is missing</h3>
        <p class="text-xs text-slate-300 max-w-md leading-relaxed mb-4">
          The screenshot file is no longer present at its stored location on disk. The capture metadata and extracted OCR text remain available below.
        </p>
      </div>
    {% endif %}
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
          <dt class="text-slate-400 font-medium">Timestamp</dt>
          <dd class="text-slate-700 font-medium mt-0.5">{{ entry.timestamp | timestamp_to_human_readable }}</dd>
        </div>
        <div>
          <dt class="text-slate-400 font-medium">Database ID</dt>
          <dd class="text-slate-700 font-mono mt-0.5">#{{ entry.id }}</dd>
        </div>
        <div>
          <dt class="text-slate-400 font-medium">File Name</dt>
          <dd class="text-slate-700 font-mono text-[11px] truncate mt-0.5" title="{{ file_name_display }}">{{ file_name_display }}</dd>
        </div>
        <div>
          <dt class="text-slate-400 font-medium">File Path</dt>
          <dd class="text-slate-600 font-mono text-[11px] truncate mt-0.5" title="{{ file_path_display }}">{{ file_path_display }}</dd>
        </div>
      </dl>

      <hr class="my-4 border-slate-100">

      <div class="flex items-center justify-between mb-2">
        <span class="text-xs font-bold text-slate-700 uppercase tracking-wider">Extracted Text</span>
        {% if entry_dict.ocr_status == 'success' %}
          <button class="px-2 py-1 text-[11px] font-semibold bg-indigo-50 text-indigo-700 hover:bg-indigo-100 rounded border border-indigo-200 transition-colors cursor-pointer" onclick="copyOcrText()">Copy Text</button>
        {% endif %}
      </div>
      {% if entry_dict.ocr_status == 'success' %}
        <pre id="ocrTextBlock" class="bg-slate-50 p-3 rounded-lg border border-slate-200 text-xs text-slate-700 font-mono max-h-60 overflow-y-auto whitespace-pre-wrap break-words leading-relaxed">{{ entry_dict.text }}</pre>
      {% elif entry_dict.ocr_status == 'empty' %}
        <p class="text-xs text-slate-500 italic bg-slate-50 p-3 rounded-lg border border-slate-200">
          OCR processed this capture, but no recognizable text was found.
        </p>
      {% elif entry_dict.ocr_status == 'failed' %}
        <div class="bg-rose-50 border border-rose-200/80 rounded-lg p-3 text-xs text-rose-900 space-y-1">
          <div class="flex items-center gap-1.5 font-semibold text-rose-800">
            <svg class="w-4 h-4 text-rose-600 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
            </svg>
            <span>OCR Process Warning</span>
          </div>
          <p class="text-[11px] text-rose-700 leading-relaxed">
            OCR execution failed or timed out during processing for this capture.
          </p>
        </div>
      {% elif entry_dict.ocr_status == 'unavailable' %}
        <div class="bg-amber-50 border border-amber-200/80 rounded-lg p-3 text-xs text-amber-900 space-y-2">
          <div class="flex items-center gap-1.5 font-semibold text-amber-800">
            <svg class="w-4 h-4 text-amber-600 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
            </svg>
            <span>OCR Unavailable</span>
          </div>
          <p class="text-[11px] text-amber-700 leading-relaxed">
            Tesseract was not detected on this system. Install Tesseract and make sure it is available in PATH to extract text from screenshots.
          </p>
          <div class="pt-1">
            <a href="https://github.com/Shiva9168/openrecall#ocr-setup" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 font-semibold text-indigo-700 hover:text-indigo-900 transition-colors text-[11px]">
              <span>View installation guide</span> &rarr;
            </a>
          </div>
        </div>
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

function toggleDeleteConfirm() {
  const card = document.getElementById('deleteConfirmCard');
  if (card) {
    card.classList.toggle('hidden');
  }
}

function toggleImageScale() {
  const img = document.getElementById('detailImg');
  const btn = document.getElementById('toggleZoomBtn');
  if (!img) return;
  if (img.classList.contains('max-h-[75vh]')) {
    img.classList.remove('max-h-[75vh]', 'w-auto', 'h-auto', 'object-contain');
    img.classList.add('max-w-none', 'w-auto');
    if (btn) btn.innerText = 'Fit to Screen';
  } else {
    img.classList.remove('max-w-none', 'w-auto');
    img.classList.add('max-h-[75vh]', 'w-auto', 'h-auto', 'object-contain');
    if (btn) btn.innerText = 'Toggle 1:1 Scale';
  }
}
</script>
{% endblock %}
""",
        entry=entry,
        entry_dict=_entry_to_dict(entry),
        file_exists=file_exists,
        file_name_display=file_name_display,
        file_path_display=file_path_display,
        prev_id=prev_id,
        next_id=next_id,
    )


@app.route("/api/capture/<int:entry_id>")
def api_capture_detail(entry_id: int):
    """REST API endpoint returning detailed metadata for a single capture ID as JSON."""
    from openrecall.database import get_entry_by_id

    entry = get_entry_by_id(entry_id)
    if not entry:
        return jsonify({"error": "Capture not found", "entry_id": entry_id}), 404
    if getattr(entry, "is_deleted", 0) == 1:
        return jsonify({"error": "Capture deleted", "entry_id": entry_id}), 410
    return jsonify(_entry_to_dict(entry))


@app.route("/api/capture/<int:entry_id>/delete", methods=["POST", "DELETE"])
def api_delete_capture(entry_id: int):
    """REST API endpoint to safely soft-delete a single capture entry and remove WebP screenshot file from disk."""
    result = delete_entry_by_id(entry_id)
    if result is None:
        return jsonify({"error": "Capture not found", "id": entry_id}), 404
    if result is False:
        return jsonify({"error": "Capture already deleted", "id": entry_id}), 410

    if request.is_json or request.headers.get("Accept") == "application/json" or request.args.get("format") == "json":
        return jsonify({"status": "success", "id": entry_id})
    return redirect("/")


@app.route("/api/timeline")
def api_timeline():
    """REST API endpoint returning paginated timeline history as JSON with date filter support."""
    page, limit, offset = _get_pagination_params()
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    start_ts = _parse_date_to_timestamp(start_date, end_of_day=False)
    end_ts = _parse_date_to_timestamp(end_date, end_of_day=True)

    entries = get_timeline_entries(
        start_time=start_ts,
        end_time=end_ts,
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
    """REST API endpoint returning paginated search results as JSON with date filter support."""
    q = request.args.get("q", "")
    page, limit, offset = _get_pagination_params()
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    start_ts = _parse_date_to_timestamp(start_date, end_of_day=False)
    end_ts = _parse_date_to_timestamp(end_date, end_of_day=True)

    entries = search_entries(
        query=q,
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


_active_pipeline = None
_active_maintenance_worker = None
_active_instance_lock = None


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """POST endpoint to request graceful shutdown of OpenRecall background process."""
    remote_addr = request.remote_addr
    if remote_addr not in ("127.0.0.1", "::1", "localhost"):
        return jsonify({"status": "error", "message": "Unauthorized request origin"}), 403

    def _do_shutdown():
        import time
        time.sleep(0.3)
        print("Shutdown requested via API. Stopping OpenRecall background process...")
        if _active_pipeline is not None:
            try:
                _active_pipeline.stop(timeout=2.0)
            except Exception:
                pass
        if _active_maintenance_worker is not None:
            try:
                _active_maintenance_worker.stop(timeout=2.0)
            except Exception:
                pass
        if _active_instance_lock is not None:
            try:
                _active_instance_lock.release()
            except Exception:
                pass
        os._exit(0)

    Thread(target=_do_shutdown, daemon=True).start()
    return jsonify({"status": "ok", "message": "OpenRecall shutdown initiated"}), 200


def stop_running_instance() -> bool:
    """Attempts to send shutdown request to running instance on port 8082."""
    import urllib.request
    import urllib.error

    url = "http://127.0.0.1:8082/api/shutdown"
    req = urllib.request.Request(
        url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            if resp.status == 200:
                print("OpenRecall stopped successfully.")
                return True
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    except Exception:
        pass

    print("OpenRecall is not currently running.")
    return False


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


def log_startup_diagnostic(stage: str, extra: str = "", exc: Optional[BaseException] = None):
    """Temporary diagnostic logger writing startup details to background-startup.log."""
    try:
        from openrecall.config import args
        log_path = os.path.join(appdata_folder, "background-startup.log")
        now_str = datetime.now(timezone.utc).isoformat()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{now_str}] STAGE: {stage} | {extra}\n")
            f.write(f"  sys.executable: {sys.executable}\n")
            f.write(f"  sys.argv: {sys.argv}\n")
            f.write(f"  cwd: {os.getcwd()}\n")
            f.write(f"  background_flag: {getattr(args, 'background', False)}\n")
            f.write(f"  appdata_folder: {appdata_folder}\n")
            if exc:
                import traceback
                f.write("  EXCEPTIONAL TRACEBACK:\n")
                f.write(traceback.format_exc())
                f.write("\n")
    except Exception:
        pass


def main():
    from openrecall.config import args, ensure_valid_standard_streams
    from openrecall.platform import get_platform_provider

    ensure_valid_standard_streams()

    if getattr(args, "stop", False):
        stop_running_instance()
        sys.exit(0)

    log_startup_diagnostic("MAIN_ENTER", "Entering openrecall.app:main()")

    if sys.platform == "win32" and getattr(args, "background", False):
        try:
            import ctypes
            ctypes.windll.kernel32.FreeConsole()
            log_startup_diagnostic("FREE_CONSOLE", "FreeConsole executed on Windows")
        except Exception as e:
            log_startup_diagnostic("FREE_CONSOLE_WARN", exc=e)

    try:
        create_db()

        # 1. Single-instance lock and duplicate startup check
        lock_file = os.path.join(appdata_folder, "openrecall.lock")
        instance_lock = SingleInstanceLock(lock_file)
        global _active_instance_lock
        _active_instance_lock = instance_lock

        target_storage_path = getattr(args, "storage_path", None)

        if not instance_lock.acquire() or check_existing_instance_running(port=8082):
            if getattr(args, "enable_autostart", False):
                if get_platform_provider().enable_startup(storage_path=target_storage_path):
                    print("Successfully enabled system autostart.")
                else:
                    print("Failed to enable system autostart.")
            elif getattr(args, "disable_autostart", False):
                if get_platform_provider().disable_startup():
                    print("Successfully disabled system autostart.")
                else:
                    print("Failed to disable system autostart.")
            else:
                print("OpenRecall is already running in the background (http://127.0.0.1:8082).")
            log_startup_diagnostic("DUPLICATE_EXIT", "Existing process detected; exiting")
            sys.exit(0)

        log_startup_diagnostic("LOCK_ACQUIRED", f"Acquired single-instance lock: {lock_file}")

        print(f"Appdata folder: {appdata_folder}")

        if getattr(args, "enable_autostart", False):
            if get_platform_provider().enable_startup(storage_path=target_storage_path):
                print("Successfully enabled system autostart.")
            else:
                print("Failed to enable system autostart.")

        if getattr(args, "disable_autostart", False):
            if get_platform_provider().disable_startup():
                print("Successfully disabled system autostart.")
            else:
                print("Failed to disable system autostart.")

        # 2. Run startup storage maintenance & orphan reconciliation
        print("Running startup storage reconciliation...")
        log_startup_diagnostic("RECONCILIATION_START")
        reconcile_storage_and_database()

        # 3. Start CapturePipeline
        log_startup_diagnostic("PIPELINE_START")
        pipeline = get_capture_pipeline()
        global _active_pipeline
        _active_pipeline = pipeline
        pipeline.start()

        # 4. Start MaintenanceWorker
        log_startup_diagnostic("MAINTENANCE_START")
        maintenance_worker = MaintenanceWorker(storage_lock=pipeline.storage_lock)
        global _active_maintenance_worker
        _active_maintenance_worker = maintenance_worker
        maintenance_worker.start()

        # 5. Graceful OS signal handling (SIGINT, SIGTERM)
        def signal_handler(sig, frame):
            print("\nShutdown signal received. Stopping background threads gracefully...")
            log_startup_diagnostic("SHUTDOWN_SIGNAL", f"Signal {sig} received")
            pipeline.stop(timeout=2.0)
            maintenance_worker.stop(timeout=2.0)
            instance_lock.release()
            sys.exit(0)

        try:
            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)
        except (ValueError, AttributeError):
            pass

        log_startup_diagnostic("FLASK_STARTING", "Running Flask web server on port 8082")
        app.run(port=8082)
    except BaseException as exc:
        log_startup_diagnostic("MAIN_CRASH", exc=exc)
        if 'instance_lock' in locals():
            try:
                instance_lock.release()
            except Exception:
                pass
        raise
    finally:
        if 'instance_lock' in locals():
            try:
                instance_lock.release()
            except Exception:
                pass


if __name__ == "__main__":
    main()
