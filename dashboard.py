#!/usr/bin/env python3
"""
dashboard.py - read-only web dashboard for the next_task.py stores.

Requires Flask (pip install flask). Everything else is stdlib. This imports
next_task.py's own functions directly rather than reimplementing any of the
dependency logic, so the dashboard can never disagree with the CLI about
what's blocked, ready, or dangling - there's exactly one place that logic
lives.

This app has no write routes at all, on purpose. It cannot create, edit, or
delete a ticket, only read what's on disk right now. Sorting and filtering
happen in the browser on rows that were already sent, so the interactive parts
need no extra routes and no round trip. It binds to localhost only, not the
network, since these stores can hold real company data.

USAGE
    python dashboard.py
    (assumes dashboard.py sits next to next_task.py, and store folders are
    siblings of both, e.g. ./personal and ./work)

    python dashboard.py --root /path/to/next-task --port 5000
    then open http://127.0.0.1:5000
"""
import argparse
from pathlib import Path

from flask import Flask, render_template_string

from next_task import (
    load_store, local_time, id_number, is_blocked, blocking_deps,
    describe_blockers, missing_deps, RESOLVED_STATUSES, PRIORITY_RANK,
)

app = Flask(__name__)
STORES_ROOT = Path(".")


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


def build_store_view(store_path: Path) -> dict:
    tickets, unreadable = load_store(store_path)
    ready, blocked, other = [], [], []
    for t in tickets.values():
        row = dict(t)
        row["_created"] = local_time(t.get("created_at"))
        row["_source"] = (t.get("source") or "").strip()
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
        "sources": sorted({r["_source"] for r in ready + blocked + other if r["_source"]}),
        "total": len(tickets),
    }


PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Tickets</title>
<style>
  body { font-family: -apple-system, "Segoe UI", sans-serif; margin: 2rem auto; max-width: 1100px; background: #f7f7f8; color: #1a1a1a; }
  h1 { margin-bottom: 0.2rem; }
  .meta { color: #666; margin-bottom: 2rem; font-size: 0.9rem; }
  .store { background: white; border: 1px solid #ddd; border-radius: 8px; padding: 1.5rem; margin-bottom: 2rem; }
  .store h2 { margin-top: 0; text-transform: capitalize; display: inline-block; }
  .count { color: #999; font-weight: normal; font-size: 0.9rem; }
  .filterbar { float: right; font-size: 0.85rem; color: #666; }
  .filterbar select { font: inherit; padding: 0.15rem 0.3rem; }
  .section { margin-bottom: 1.5rem; clear: both; }
  .section h3 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; color: #888; margin-bottom: 0.4rem; }
  table { border-collapse: collapse; width: 100%; font-size: 0.88rem; }
  th { text-align: left; font-weight: 600; font-size: 0.72rem; text-transform: uppercase;
       letter-spacing: 0.04em; color: #999; border-bottom: 1px solid #e3e3e3; padding: 0.3rem 0.5rem; }
  th[data-sort-key] { cursor: pointer; user-select: none; }
  th[data-sort-key]:hover { color: #333; }
  th.asc::after { content: " \\2191"; }
  th.desc::after { content: " \\2193"; }
  td { padding: 0.35rem 0.5rem; border-bottom: 1px solid #f2f2f2; vertical-align: top; }
  tbody tr.ready td:first-child { border-left: 3px solid #2e7d32; }
  tbody tr.blocked td:first-child { border-left: 3px solid #c62828; }
  tbody tr.doing td:first-child { border-left: 3px solid #ef6c00; }
  .id { font-weight: 600; font-family: Consolas, monospace; color: #333; white-space: nowrap; }
  .date { color: #999; font-family: Consolas, monospace; white-space: nowrap; }
  .src { color: #555; background: #eee; border-radius: 3px; padding: 0.05rem 0.35rem; font-size: 0.78rem; white-space: nowrap; }
  .priority-critical { color: #c62828; font-weight: 700; text-transform: uppercase; font-size: 0.72rem; }
  .priority-high { color: #ef6c00; font-weight: 600; font-size: 0.75rem; }
  .priority-medium { color: #777; font-size: 0.75rem; }
  .priority-low { color: #aaa; font-size: 0.75rem; }
  .state { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.03em; color: #999; white-space: nowrap; }
  .state.doing { color: #ef6c00; font-weight: 700; }
  .sub { color: #777; font-size: 0.8rem; margin-top: 0.15rem; }
  .warn { color: #c62828; font-size: 0.85rem; margin-bottom: 1rem; }
  details summary { cursor: pointer; color: #666; font-size: 0.85rem; margin-bottom: 0.6rem; }
  .empty { color: #999; font-style: italic; font-size: 0.9rem; }
</style>
</head>
<body>
<h1>Tickets</h1>
<p class="meta">Read-only, reflects what's on disk right now. Refresh to update.
Sorting and filtering happen in your browser &mdash; nothing is written from here.</p>

{% for s in stores %}
<div class="store">
  {% if s.sources %}
  <div class="filterbar">
    Source
    <select class="filter">
      <option value="">all</option>
      {% for src in s.sources %}<option value="{{ src }}">{{ src }}</option>{% endfor %}
    </select>
  </div>
  {% endif %}
  <h2>{{ s.name }} <span class="count">({{ s.total }} tickets)</span></h2>

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
    <table>
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
        <tr class="{{ 'doing' if t.status == 'in_progress' else 'ready' }}" data-source="{{ t._source }}">
          <td class="id" data-sort="{{ t._num }}">{{ t.id }}</td>
          <td class="date" data-sort="{{ t.created_at }}">{{ t._created }}</td>
          <td data-sort="{{ t._source }}">{% if t._source %}<span class="src">{{ t._source }}</span>{% endif %}</td>
          <td class="priority-{{ t.priority }}" data-sort="{{ t._rank }}">{{ t.priority }}</td>
          <td class="state {{ 'doing' if t.status == 'in_progress' else '' }}" data-sort="{{ t.status }}">{{ t.status.replace('_', ' ') }}</td>
          <td>{{ t.title }}
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
    <table>
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
        <tr class="blocked" data-source="{{ t._source }}">
          <td class="id" data-sort="{{ t._num }}">{{ t.id }}</td>
          <td class="date" data-sort="{{ t.created_at }}">{{ t._created }}</td>
          <td data-sort="{{ t._source }}">{% if t._source %}<span class="src">{{ t._source }}</span>{% endif %}</td>
          <td class="priority-{{ t.priority }}" data-sort="{{ t._rank }}">{{ t.priority }}</td>
          <td>{{ t.title }}</td>
          <td class="sub">{{ t._blocked_on|join(', ') }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
    {% endif %}
  </div>

  <details>
    <summary>Done / cancelled &middot; {{ s.other|length }}</summary>
    {% if s.other %}
    <table>
      <thead><tr>
        <th data-sort-key="id">ID</th>
        <th data-sort-key="created">Created</th>
        <th data-sort-key="source">Source</th>
        <th data-sort-key="state">State</th>
        <th>Title</th>
      </tr></thead>
      <tbody>
      {% for t in s.other %}
        <tr data-source="{{ t._source }}">
          <td class="id" data-sort="{{ t._num }}">{{ t.id }}</td>
          <td class="date" data-sort="{{ t.created_at }}">{{ t._created }}</td>
          <td data-sort="{{ t._source }}">{% if t._source %}<span class="src">{{ t._source }}</span>{% endif %}</td>
          <td class="state" data-sort="{{ t.status }}">{{ t.status }}</td>
          <td>{{ t.title }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
    {% endif %}
  </details>
</div>
{% endfor %}

{% if not stores %}
<p class="empty">No stores found under {{ root }}. Expected subfolders that each contain a "tickets" folder.</p>
{% endif %}

<script>
// Sorting and filtering run entirely on rows already in the page. No requests,
// no state on the server, and so no route that could ever write anything.
document.querySelectorAll('.store').forEach(function (store) {
  var select = store.querySelector('.filter');

  function recount() {
    store.querySelectorAll('.section').forEach(function (section) {
      var counter = section.querySelector('.n');
      if (!counter) return;
      counter.textContent = section.querySelectorAll('tbody tr:not([hidden])').length;
    });
  }

  if (select) {
    select.addEventListener('change', function () {
      var want = select.value;
      store.querySelectorAll('tbody tr').forEach(function (row) {
        row.hidden = Boolean(want) && row.dataset.source !== want;
      });
      recount();
    });
  }

  store.querySelectorAll('th[data-sort-key]').forEach(function (th) {
    th.addEventListener('click', function () {
      var head = th.parentNode;
      var index = Array.prototype.indexOf.call(head.children, th);
      var ascending = th.dataset.dir !== 'asc';
      head.querySelectorAll('th').forEach(function (other) {
        other.dataset.dir = '';
        other.classList.remove('asc', 'desc');
      });
      th.dataset.dir = ascending ? 'asc' : 'desc';
      th.classList.add(ascending ? 'asc' : 'desc');

      var body = th.closest('table').querySelector('tbody');
      var rows = Array.prototype.slice.call(body.querySelectorAll('tr'));
      rows.sort(function (a, b) {
        var left = a.children[index].dataset.sort;
        var right = b.children[index].dataset.sort;
        if (left === undefined) left = a.children[index].textContent.trim();
        if (right === undefined) right = b.children[index].textContent.trim();
        var ln = parseFloat(left), rn = parseFloat(right);
        var result = (!isNaN(ln) && !isNaN(rn)) ? ln - rn : String(left).localeCompare(String(right));
        return ascending ? result : -result;
      });
      rows.forEach(function (row) { body.appendChild(row); });
    });
  });
});
</script>
</body>
</html>
"""


@app.route("/")
def index():
    stores = [build_store_view(p) for p in discover_stores(STORES_ROOT)]
    return render_template_string(PAGE, stores=stores, root=str(STORES_ROOT))


def main():
    global STORES_ROOT
    parser = argparse.ArgumentParser(description="Read-only dashboard for next_task.py stores.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent),
                         help="Folder containing store subfolders (each with its own tickets/ subfolder)")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    STORES_ROOT = Path(args.root)
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
