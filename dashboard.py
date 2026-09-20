#!/usr/bin/env python3
"""
dashboard.py - web dashboard for the next_task.py stores.

Requires Flask (pip install flask). Everything else is stdlib. This imports
next_task.py's own functions directly rather than reimplementing any of the
dependency logic, so the dashboard can never disagree with the CLI about
what's blocked, ready, or dangling - there's exactly one place that logic
lives.

The page is one route serving three tabs: one per store, plus a shared
"Done / cancelled" tab that merges the finished tickets of every store.
Sorting, filtering (by source and by a title/description search box), tab
switching and description unfurling all happen in the browser on rows that
were already sent, so none of it needs a route or a round trip. The chosen
tab, sort order and light/dark theme are remembered in the browser
(localStorage), never on the server; the search box and source filter are
not, matching each other. With no saved theme the page follows the operating
system's setting. Every colour lives in one of two palettes of CSS variables,
and a test fails if a rule hardcodes one, which is how dark modes go wrong.

WRITE ACCESS - deliberately almost none. There is exactly one write route,
POST /delete, which exists so old finished tickets can be cleared out from
the Done / cancelled tab. It is guarded three ways:
  - it only deletes tickets whose status is done or cancelled, checked here
    against what's on disk, not against what the browser claimed;
  - the actual deletion shells out to next_task.py's own delete command, so
    the CLI's dependents guard applies: a ticket something still depends on
    is refused, because a missing prerequisite counts as blocking and
    deleting it would re-block live work;
  - it accepts JSON only. A cross-site form can POST to localhost, but a
    cross-site JSON request can't without a CORS preflight this app never
    answers, so requiring JSON is the CSRF guard. An Origin header naming
    anywhere but this machine is refused outright.
Everything else serves GET and nothing but GET; a test asserts exactly that
boundary. The app binds to localhost only, not the network, since these
stores can hold real company data.

USAGE
    python dashboard.py
    (assumes dashboard.py sits next to next_task.py, and store folders are
    siblings of both, e.g. ./personal and ./work)

    python dashboard.py --root /path/to/next-task --port 5000
    then open http://127.0.0.1:5000
"""
import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

from next_task import (
    load_store, local_time, id_number, is_blocked, blocking_deps,
    describe_blockers, missing_deps, RESOLVED_STATUSES, PRIORITY_RANK,
)

app = Flask(__name__)
STORES_ROOT = Path(".")
CLI = Path(__file__).resolve().parent / "next_task.py"


def discover_stores(root: Path) -> list:
    """Any subfolder of root that itself contains a 'tickets' folder counts
    as a store. No hardcoded list of store names, so adding a third store
    later needs zero code changes here."""
    if not root.exists():
        return []
    return sorted(
        child for child in root.iterdir()
        if child.is_dir() and (child / "tickets").is_dir()
    )


def would_unlock(ticket_id: str, tickets: dict) -> list:
    """Tickets that would become ready if ticket_id were completed right now."""
    result = []
    for other in tickets.values():
        if ticket_id not in other.get("depends_on", []):
            continue
        if other["status"] in RESOLVED_STATUSES:
            continue
        remaining = [d for d in blocking_deps(other, tickets) if d != ticket_id]
        if not remaining:
            result.append(other["id"])
    return result


def iso_epoch(iso) -> float:
    """A stored timestamp as a plain number, for the browser to sort by.

    The sort script compares numbers numerically and everything else as text.
    An ISO timestamp must not be handed over as text: every one starts with
    the year, and a numeric parse of a leading prefix made all rows compare
    equal, which is exactly the bug that left the Created column refusing to
    sort. Anything unparseable becomes 0 and sorts to one end rather than
    stopping the page."""
    try:
        when = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


def build_store_view(store_path: Path) -> dict:
    tickets, unreadable = load_store(store_path)
    ready, blocked, other = [], [], []
    for t in tickets.values():
        row = dict(t)
        row["_created"] = local_time(t.get("created_at"))
        row["_created_num"] = iso_epoch(t.get("created_at"))
        row["_updated"] = local_time(t.get("updated_at"))
        row["_updated_num"] = iso_epoch(t.get("updated_at"))
        row["_source"] = (t.get("source") or "").strip()
        row["_desc"] = (t.get("description") or "").strip()
        row["_search"] = f"{t['title']} {row['_desc']}".strip().lower()
        row["_rank"] = PRIORITY_RANK.get(t.get("priority"), 1)
        row["_num"] = id_number(t["id"])
        if t["status"] in RESOLVED_STATUSES:
            other.append(row)
        elif is_blocked(t, tickets):
            row["_blocked_on"] = describe_blockers(t, tickets)
            blocked.append(row)
        else:
            row["_unlocks"] = would_unlock(t["id"], tickets)
            ready.append(row)
    ready.sort(key=lambda t: (-t["_rank"], t["_num"]))
    blocked.sort(key=lambda t: t["_num"])
    other.sort(key=lambda t: t["_num"], reverse=True)
    dangling = [t for t in tickets.values() if missing_deps(t, tickets)]
    return {
        "name": store_path.name,
        "ready": ready,
        "blocked": blocked,
        "other": other,
        "dangling": dangling,
        "unreadable": unreadable,
        "sources": sorted({r["_source"] for r in ready + blocked if r["_source"]}),
    }


def finished_rows(views: list) -> list:
    """Done and cancelled tickets from every store, merged into the rows for
    the shared tab: each one naming its store, most recently touched first.
    updated_at is the closest thing on disk to "when was this finished"."""
    rows = []
    for view in views:
        for row in view["other"]:
            row = dict(row)
            row["_store"] = view["name"]
            rows.append(row)
    rows.sort(key=lambda r: r["_updated_num"], reverse=True)
    return rows


PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Tickets</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' rx='3' fill='%232e7d32'/%3E%3Cpath d='M4 8.5 7 11.5 12 5' fill='none' stroke='%23fff' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E">
<script>
// Runs before anything paints, so a dark-mode user never sees a white flash.
// A saved choice wins; otherwise follow the operating system's setting.
(function () {
  var theme = null;
  try { theme = localStorage.getItem('tickets.theme'); } catch (e) {}
  if (theme !== 'dark' && theme !== 'light') {
    theme = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  document.documentElement.setAttribute('data-theme', theme);
})();
</script>
<style>
  :root {
    --bg: #f7f7f8; --text: #1a1a1a; --muted: #666; --dim: #888; --faint: #999; --low: #aaa;
    --surface: #ffffff; --surface-alt: #efeff1; --desc-bg: #f7f7f8;
    --border: #ddd; --border-strong: #bbb; --input-border: #ccc; --rule: #e3e3e3; --rule-soft: #f2f2f2;
    --id: #333; --hover: #333; --chip-bg: #eee; --chip-text: #555; --desc-text: #555; --sub: #777; --chev: #bbb;
    --ready: #2e7d32; --blocked: #c62828; --doing: #ef6c00; --on-accent: #ffffff;
    color-scheme: light;
  }
  :root[data-theme="dark"] {
    --bg: #16171a; --text: #e6e6e6; --muted: #a0a4ab; --dim: #8b8f96; --faint: #8b8f96; --low: #6f737a;
    --surface: #202226; --surface-alt: #1b1c1f; --desc-bg: #1a1b1e;
    --border: #33363b; --border-strong: #4a4e55; --input-border: #4a4e55; --rule: #2f3237; --rule-soft: #292b30;
    --id: #d8d8d8; --hover: #ffffff; --chip-bg: #2c2f35; --chip-text: #c4c7cc; --desc-text: #c4c7cc; --sub: #9a9ea5; --chev: #6b6f76;
    --ready: #4caf50; --blocked: #ef5350; --doing: #ffa726; --on-accent: #ffffff;
    color-scheme: dark;
  }
  body { font-family: -apple-system, "Segoe UI", sans-serif; margin: 2rem auto; max-width: 1100px; background: var(--bg); color: var(--text); }
  .top { display: flex; justify-content: space-between; align-items: flex-end; gap: 1rem; }
  h1 { margin-bottom: 0.2rem; }
  #theme-toggle { font: inherit; font-size: 0.85rem; padding: 0.3rem 0.8rem; border: 1px solid var(--border);
                  background: var(--surface); color: var(--muted); border-radius: 6px; cursor: pointer; }
  #theme-toggle:hover { color: var(--text); border-color: var(--border-strong); }
  .meta { color: var(--muted); margin-bottom: 1.2rem; font-size: 0.9rem; }
  .tabs { display: flex; gap: 0.4rem; margin-bottom: 1rem; flex-wrap: wrap; }
  .tab { font: inherit; font-size: 0.9rem; padding: 0.45rem 1.1rem; border: 1px solid var(--border);
         background: var(--surface-alt); border-radius: 6px; cursor: pointer; color: var(--muted); text-transform: capitalize; }
  .tab:hover { color: var(--text); }
  .tab.active { background: var(--surface); font-weight: 600; color: var(--text); border-color: var(--border-strong); }
  .tab .count { margin-left: 0.3rem; }
  .panel { display: none; }
  .panel.active { display: block; }
  .store { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; margin-bottom: 2rem; }
  .store h2 { margin-top: 0; text-transform: capitalize; display: inline-block; }
  .count { color: var(--faint); font-weight: normal; font-size: 0.9rem; }
  .filterbar { float: right; font-size: 0.85rem; color: var(--muted); }
  .filterbar select { font: inherit; padding: 0.15rem 0.3rem; }
  .filterbar input.search { font: inherit; padding: 0.15rem 0.5rem; border: 1px solid var(--input-border);
                            border-radius: 4px; width: 12rem; margin-right: 0.6rem; }
  .section { margin-bottom: 1.5rem; clear: both; }
  .section h3 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--dim); margin-bottom: 0.4rem; }
  table { border-collapse: collapse; width: 100%; font-size: 0.88rem; }
  th { white-space: nowrap; text-align: left; font-weight: 600; font-size: 0.72rem; text-transform: uppercase;
       letter-spacing: 0.04em; color: var(--faint); border-bottom: 1px solid var(--rule); padding: 0.3rem 0.5rem; }
  th[data-sort-key] { cursor: pointer; user-select: none; }
  th[data-sort-key]:hover { color: var(--hover); }
  th.asc::after { content: "\\00a0\\2191"; }
  th.desc::after { content: "\\00a0\\2193"; }
  td { padding: 0.35rem 0.5rem; border-bottom: 1px solid var(--rule-soft); vertical-align: top; }
  tbody tr.ready td:first-child { border-left: 3px solid var(--ready); }
  tbody tr.blocked td:first-child { border-left: 3px solid var(--blocked); }
  tbody tr.doing td:first-child { border-left: 3px solid var(--doing); }
  .id { font-weight: 600; font-family: Consolas, monospace; color: var(--id); white-space: nowrap; }
  .date { color: var(--faint); font-family: Consolas, monospace; white-space: nowrap; }
  .src { color: var(--chip-text); background: var(--chip-bg); border-radius: 3px; padding: 0.05rem 0.35rem; font-size: 0.78rem; white-space: nowrap; }
  .priority-critical { color: var(--blocked); font-weight: 700; text-transform: uppercase; font-size: 0.72rem; }
  .priority-high { color: var(--doing); font-weight: 600; font-size: 0.75rem; }
  .priority-medium { color: var(--sub); font-size: 0.75rem; }
  .priority-low { color: var(--low); font-size: 0.75rem; }
  .state { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.03em; color: var(--faint); white-space: nowrap; }
  .state.doing { color: var(--doing); font-weight: 700; }
  .sub { color: var(--sub); font-size: 0.8rem; margin-top: 0.15rem; }
  .warn { color: var(--blocked); font-size: 0.85rem; margin-bottom: 1rem; }
  .empty { color: var(--faint); font-style: italic; font-size: 0.9rem; }
  td.has-desc { cursor: pointer; }
  .chev { color: var(--chev); font-size: 0.7rem; margin-left: 0.35rem; display: inline-block; transition: transform 0.1s; }
  td.has-desc.open .chev { transform: rotate(90deg); }
  .desc { color: var(--desc-text); font-size: 0.83rem; margin-top: 0.35rem; padding: 0.5rem 0.6rem;
          background: var(--desc-bg); border-radius: 4px; white-space: pre-wrap; }
  .deletebar { margin-bottom: 0.8rem; clear: both; }
  .deletebar button { font: inherit; font-size: 0.85rem; padding: 0.35rem 0.9rem; border-radius: 5px;
                      border: 1px solid var(--blocked); background: var(--surface); color: var(--blocked); cursor: pointer; }
  .deletebar button:disabled { border-color: var(--border); color: var(--low); cursor: default; }
  .deletebar button:not(:disabled):hover { background: var(--blocked); color: var(--on-accent); }
  .pickcol { width: 1.5rem; }
</style>
</head>
<body>
<header class="top">
  <h1>Tickets</h1>
  <button id="theme-toggle" type="button">Theme</button>
</header>
<p class="meta">Reflects what's on disk right now &mdash; refresh to update. Sorting, filtering
and tabs run in your browser. The one thing this page can change is deleting
finished tickets from the Done / cancelled tab.</p>

<nav class="tabs">
  {% for s in stores %}
  <button class="tab" data-tab="{{ s.name }}">{{ s.name }} <span class="count">{{ s.ready|length + s.blocked|length }}</span></button>
  {% endfor %}
  <button class="tab" data-tab="finished">Done / cancelled <span class="count">{{ finished|length }}</span></button>
</nav>

{% for s in stores %}
<div class="store panel" id="panel-{{ s.name }}">
  <div class="filterbar">
    <input type="search" class="search" placeholder="Search title &amp; description">
    {% if s.sources %}
    Source
    <select class="filter">
      <option value="">all</option>
      {% for src in s.sources %}<option value="{{ src }}">{{ src }}</option>{% endfor %}
    </select>
    {% endif %}
  </div>
  <h2>{{ s.name }}</h2>

  {% if s.unreadable %}
  <p class="warn">{{ s.unreadable|length }} file(s) in this store could not be read and are
  not shown below &mdash; run <code>next_task.py --store ... verify</code> for detail.</p>
  {% endif %}

  {% if s.dangling %}
  <p class="warn">{{ s.dangling|length }} ticket(s) reference a missing dependency &mdash;
  run <code>next_task.py --store ... verify</code> on this store.</p>
  {% endif %}

  <div class="section">
    <h3>Ready &middot; <span class="n">{{ s.ready|length }}</span></h3>
    {% if not s.ready %}<p class="empty">Nothing ready.</p>{% else %}
    <table data-table="{{ s.name }}-ready">
      <thead><tr>
        <th data-sort-key="id">ID</th>
        <th data-sort-key="created">Created</th>
        <th data-sort-key="source">Source</th>
        <th data-sort-key="priority">Priority</th>
        <th data-sort-key="state">State</th>
        <th>Title</th>
      </tr></thead>
      <tbody>
      {% for t in s.ready %}
        <tr class="{{ 'doing' if t.status == 'in_progress' else 'ready' }}" data-source="{{ t._source }}" data-search="{{ t._search }}">
          <td class="id" data-sort="{{ t._num }}">{{ t.id }}</td>
          <td class="date" data-sort="{{ t._created_num }}">{{ t._created }}</td>
          <td data-sort="{{ t._source }}">{% if t._source %}<span class="src">{{ t._source }}</span>{% endif %}</td>
          <td class="priority-{{ t.priority }}" data-sort="{{ t._rank }}">{{ t.priority }}</td>
          <td class="state {{ 'doing' if t.status == 'in_progress' else '' }}" data-sort="{{ t.status }}">{{ t.status.replace('_', ' ') }}</td>
          <td{% if t._desc %} class="has-desc"{% endif %}>{{ t.title }}{% if t._desc %}<span class="chev">&#9656;</span>
            <div class="desc" hidden>{{ t._desc }}</div>{% endif %}
            {% if t._unlocks %}<div class="sub">Finishing this unlocks: {{ t._unlocks|join(', ') }}</div>{% endif %}
          </td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
    {% endif %}
  </div>

  <div class="section">
    <h3>Blocked &middot; <span class="n">{{ s.blocked|length }}</span></h3>
    {% if not s.blocked %}<p class="empty">Nothing blocked.</p>{% else %}
    <table data-table="{{ s.name }}-blocked">
      <thead><tr>
        <th data-sort-key="id">ID</th>
        <th data-sort-key="created">Created</th>
        <th data-sort-key="source">Source</th>
        <th data-sort-key="priority">Priority</th>
        <th>Title</th>
        <th>Waiting on</th>
      </tr></thead>
      <tbody>
      {% for t in s.blocked %}
        <tr class="blocked" data-source="{{ t._source }}" data-search="{{ t._search }}">
          <td class="id" data-sort="{{ t._num }}">{{ t.id }}</td>
          <td class="date" data-sort="{{ t._created_num }}">{{ t._created }}</td>
          <td data-sort="{{ t._source }}">{% if t._source %}<span class="src">{{ t._source }}</span>{% endif %}</td>
          <td class="priority-{{ t.priority }}" data-sort="{{ t._rank }}">{{ t.priority }}</td>
          <td{% if t._desc %} class="has-desc"{% endif %}>{{ t.title }}{% if t._desc %}<span class="chev">&#9656;</span>
            <div class="desc" hidden>{{ t._desc }}</div>{% endif %}</td>
          <td class="sub">{{ t._blocked_on|join(', ') }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
    {% endif %}
  </div>
</div>
{% endfor %}

<div class="store panel" id="panel-finished">
  <div class="filterbar">
    <input type="search" class="search" placeholder="Search title &amp; description">
    {% if finished_sources %}
    Source
    <select class="filter">
      <option value="">all</option>
      {% for src in finished_sources %}<option value="{{ src }}">{{ src }}</option>{% endfor %}
    </select>
    {% endif %}
  </div>
  <h2>Done / cancelled <span class="count">(all stores)</span></h2>

  {% if not finished %}
  <p class="empty">Nothing finished yet.</p>
  {% else %}
  <div class="deletebar">
    <button id="delete-selected" disabled>Delete selected</button>
    <span class="count">Deleting is permanent; a ticket something still depends on is refused.</span>
  </div>
  <div class="section">
    <h3>Finished &middot; <span class="n">{{ finished|length }}</span></h3>
    <table data-table="finished">
      <thead><tr>
        <th class="pickcol"><input type="checkbox" id="select-all-finished" title="Select all"></th>
        <th data-sort-key="store">Store</th>
        <th data-sort-key="id">ID</th>
        <th data-sort-key="created">Created</th>
        <th data-sort-key="updated">Updated</th>
        <th data-sort-key="source">Source</th>
        <th data-sort-key="state">State</th>
        <th>Title</th>
      </tr></thead>
      <tbody>
      {% for t in finished %}
        <tr data-source="{{ t._source }}" data-search="{{ t._search }}">
          <td class="pickcol"><input type="checkbox" class="pick" data-store="{{ t._store }}" data-id="{{ t.id }}"></td>
          <td data-sort="{{ t._store }}">{{ t._store }}</td>
          <td class="id" data-sort="{{ t._num }}">{{ t.id }}</td>
          <td class="date" data-sort="{{ t._created_num }}">{{ t._created }}</td>
          <td class="date" data-sort="{{ t._updated_num }}">{{ t._updated }}</td>
          <td data-sort="{{ t._source }}">{% if t._source %}<span class="src">{{ t._source }}</span>{% endif %}</td>
          <td class="state" data-sort="{{ t.status }}">{{ t.status }}</td>
          <td{% if t._desc %} class="has-desc"{% endif %}>{{ t.title }}{% if t._desc %}<span class="chev">&#9656;</span>
            <div class="desc" hidden>{{ t._desc }}</div>{% endif %}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}
</div>

{% if not stores %}
<p class="empty">No stores found under {{ root }}. Expected subfolders that each contain a "tickets" folder.</p>
{% endif %}

<script>
// Tabs, sorting, filtering and unfurling all run on rows already in the page.
// The only request the page can make is the delete call at the bottom.
(function () {
  function remember(key, value) { try { localStorage.setItem(key, value); } catch (e) {} }
  function recall(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }

  // ---- Theme. The head script has already applied it; this is the switch.
  var themeToggle = document.getElementById('theme-toggle');
  function showThemeLabel() {
    var dark = document.documentElement.getAttribute('data-theme') === 'dark';
    themeToggle.textContent = dark ? 'Light theme' : 'Dark theme';
    themeToggle.title = dark ? 'Switch to the light theme' : 'Switch to the dark theme';
  }
  themeToggle.addEventListener('click', function () {
    var dark = document.documentElement.getAttribute('data-theme') === 'dark';
    document.documentElement.setAttribute('data-theme', dark ? 'light' : 'dark');
    remember('tickets.theme', dark ? 'light' : 'dark');
    showThemeLabel();
  });
  showThemeLabel();

  // ---- Tabs. The active one is remembered across refreshes.
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.tab'));
  function showTab(name) {
    tabs.forEach(function (tab) { tab.classList.toggle('active', tab.dataset.tab === name); });
    document.querySelectorAll('.panel').forEach(function (panel) {
      panel.classList.toggle('active', panel.id === 'panel-' + name);
    });
    remember('tickets.tab', name);
  }
  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () { showTab(tab.dataset.tab); });
  });
  var names = tabs.map(function (tab) { return tab.dataset.tab; });
  var savedTab = recall('tickets.tab');
  if (names.length) showTab(names.indexOf(savedTab) >= 0 ? savedTab : names[0]);

  // ---- Sorting. Numeric only when the WHOLE value is a number: a leading
  // number alone must fall through to text, or every ISO timestamp parses
  // as its year and the column refuses to sort.
  var wholeNumber = /^-?\\d+(\\.\\d+)?$/;
  function compareValues(left, right) {
    if (wholeNumber.test(left) && wholeNumber.test(right)) return parseFloat(left) - parseFloat(right);
    return String(left).localeCompare(String(right));
  }
  function cellValue(row, index) {
    var cell = row.children[index];
    if (!cell) return '';
    return cell.dataset.sort === undefined ? cell.textContent.trim() : cell.dataset.sort;
  }
  function applySort(table, key, direction) {
    var th = table.querySelector('th[data-sort-key="' + key + '"]');
    if (!th) return;
    var head = th.parentNode;
    var index = Array.prototype.indexOf.call(head.children, th);
    head.querySelectorAll('th').forEach(function (other) {
      other.dataset.dir = '';
      other.classList.remove('asc', 'desc');
    });
    th.dataset.dir = direction;
    th.classList.add(direction);
    var body = table.querySelector('tbody');
    var rows = Array.prototype.slice.call(body.querySelectorAll('tr'));
    rows.sort(function (a, b) {
      var result = compareValues(cellValue(a, index), cellValue(b, index));
      return direction === 'asc' ? result : -result;
    });
    rows.forEach(function (row) { body.appendChild(row); });
  }
  document.querySelectorAll('table[data-table]').forEach(function (table) {
    table.querySelectorAll('th[data-sort-key]').forEach(function (th) {
      th.addEventListener('click', function () {
        var direction = th.dataset.dir === 'asc' ? 'desc' : 'asc';
        applySort(table, th.dataset.sortKey, direction);
        remember('tickets.sort.' + table.dataset.table, th.dataset.sortKey + ':' + direction);
      });
    });
    var savedSort = recall('tickets.sort.' + table.dataset.table);
    if (savedSort) {
      var parts = savedSort.split(':');
      applySort(table, parts[0], parts[1] === 'desc' ? 'desc' : 'asc');
    }
  });

  // ---- Source filter and search box, one pair per tab panel. Both must
  // match for a row to show; either can be absent (a store with no sources
  // gets no dropdown, but always gets a search box).
  document.querySelectorAll('.panel').forEach(function (panel) {
    var select = panel.querySelector('.filter');
    var search = panel.querySelector('.search');
    if (!select && !search) return;
    function apply() {
      var want = select ? select.value : '';
      var query = search ? search.value.trim().toLowerCase() : '';
      panel.querySelectorAll('tbody tr').forEach(function (row) {
        var sourceOk = !want || row.dataset.source === want;
        var searchOk = !query || (row.dataset.search || '').indexOf(query) !== -1;
        row.hidden = !(sourceOk && searchOk);
      });
      panel.querySelectorAll('.section').forEach(function (section) {
        var counter = section.querySelector('.n');
        if (counter) counter.textContent = section.querySelectorAll('tbody tr:not([hidden])').length;
      });
      refreshDeleteButton();
    }
    if (select) select.addEventListener('change', apply);
    if (search) search.addEventListener('input', apply);
  });

  // ---- Descriptions unfurl in place. Clicking anywhere else rolls them up.
  document.addEventListener('click', function (event) {
    if (event.target.closest('a, button, select, input, th')) return;
    var row = event.target.closest('tr');
    var cell = row ? row.querySelector('td.has-desc') : null;
    document.querySelectorAll('td.has-desc.open').forEach(function (other) {
      if (other !== cell) {
        other.classList.remove('open');
        other.querySelector('.desc').hidden = true;
      }
    });
    if (cell) {
      var open = !cell.classList.contains('open');
      cell.classList.toggle('open', open);
      cell.querySelector('.desc').hidden = !open;
    }
  });

  // ---- Deleting finished tickets. Selection, confirmation, one POST.
  var deleteButton = document.getElementById('delete-selected');
  var selectAll = document.getElementById('select-all-finished');
  function selectedBoxes() {
    return Array.prototype.slice.call(
      document.querySelectorAll('#panel-finished input.pick:checked')
    ).filter(function (box) { return !box.closest('tr').hidden; });
  }
  function refreshDeleteButton() {
    if (!deleteButton) return;
    var n = selectedBoxes().length;
    deleteButton.disabled = n === 0;
    deleteButton.textContent = n ? 'Delete ' + n + ' selected' : 'Delete selected';
  }
  if (selectAll) {
    selectAll.addEventListener('change', function () {
      document.querySelectorAll('#panel-finished tbody tr').forEach(function (row) {
        var box = row.querySelector('input.pick');
        if (box && !row.hidden) box.checked = selectAll.checked;
      });
      refreshDeleteButton();
    });
  }
  document.querySelectorAll('#panel-finished input.pick').forEach(function (box) {
    box.addEventListener('change', refreshDeleteButton);
  });
  if (deleteButton) {
    deleteButton.addEventListener('click', function () {
      var boxes = selectedBoxes();
      if (!boxes.length) return;
      var ids = boxes.map(function (box) { return box.dataset.id; });
      if (!confirm('Permanently delete ' + boxes.length + ' finished ticket(s)?\\n\\n'
                   + ids.join(', ') + '\\n\\nThere is no undo.')) return;
      deleteButton.disabled = true;
      fetch('/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tickets: boxes.map(function (box) {
          return { store: box.dataset.store, id: box.dataset.id };
        }) })
      }).then(function (response) { return response.json(); })
        .then(function (result) {
          if (result.refused && result.refused.length) {
            alert('Not deleted:\\n' + result.refused.map(function (r) {
              return r.id + ' - ' + r.reason;
            }).join('\\n'));
          }
          location.reload();
        })
        .catch(function (error) { alert('Delete failed: ' + error); location.reload(); });
    });
  }
  refreshDeleteButton();
})();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    stores = [build_store_view(p) for p in discover_stores(STORES_ROOT)]
    finished = finished_rows(stores)
    return render_template_string(
        PAGE, stores=stores, finished=finished,
        finished_sources=sorted({r["_source"] for r in finished if r["_source"]}),
        root=str(STORES_ROOT))


@app.route("/delete", methods=["POST"])
def delete_finished():
    """The one write route. See the module docstring for the three guards."""
    origin = request.headers.get("Origin", "")
    if origin and not (origin.startswith("http://127.0.0.1")
                       or origin.startswith("http://localhost")):
        return jsonify(error="cross-origin request refused"), 403
    if not request.is_json:
        return jsonify(error="JSON only"), 415
    payload = request.get_json(silent=True) or {}
    asked = payload.get("tickets") or []
    stores = {p.name: p for p in discover_stores(STORES_ROOT)}
    loaded = {}
    deleted, refused = [], []
    pending = []
    for item in asked:
        store_name = str(item.get("store") or "")
        tid = str(item.get("id") or "")
        store = stores.get(store_name)
        if store is None:
            refused.append({"id": tid, "reason": f"no store called {store_name!r}"})
            continue
        if store_name not in loaded:
            loaded[store_name], _ = load_store(store)
        ticket = loaded[store_name].get(tid)
        if ticket is None:
            refused.append({"id": tid, "reason": "no such ticket"})
            continue
        if ticket["status"] not in RESOLVED_STATUSES:
            refused.append({"id": tid,
                            "reason": f"status is {ticket['status']}, not done/cancelled"})
            continue
        pending.append((store, tid))
    # The deletion itself is the CLI's, dependents guard included - the same
    # reasoning as the MCP server: writes go through the one real code path.
    # A finished chain selected together needs the dependent deleted before
    # its prerequisite, so keep passing over the survivors while anything at
    # all succeeds; whatever the CLI still refuses at the end is reported in
    # its own words.
    last_error = {}
    progress = True
    while pending and progress:
        progress = False
        remaining = []
        for store, tid in pending:
            proc = subprocess.run(
                [sys.executable, str(CLI), "--store", str(store), "delete", tid],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            if proc.returncode == 0:
                deleted.append(tid)
                progress = True
            else:
                last_error[tid] = (proc.stderr or "").strip() or "could not delete"
                remaining.append((store, tid))
        pending = remaining
    for store, tid in pending:
        refused.append({"id": tid, "reason": last_error[tid]})
    return jsonify(deleted=deleted, refused=refused)


def main():
    global STORES_ROOT
    parser = argparse.ArgumentParser(description="Web dashboard for next_task.py stores.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent),
                         help="Folder containing store subfolders (each with its own tickets/ subfolder)")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    STORES_ROOT = Path(args.root)
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
