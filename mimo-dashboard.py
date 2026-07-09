#!/usr/bin/env python3
"""Generate TokenPulse Dashboard as a self-contained HTML file.
Reads from MiMo Code SQLite database AND 9Router API."""

import sqlite3
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime
from collections import defaultdict

DB_PATH = os.path.expanduser("~/.local/share/mimocode/mimocode.db")
ROUTER_URL = "http://localhost:20128"
ROUTER_PASSWORD = "123456"
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mimo-token-dashboard.html")


def fetch_mimo_data(db_path):
    """Fetch token usage data from MiMo Code SQLite database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT
            m.data,
            m.time_created,
            m.agent_id,
            m.session_id,
            s.title as session_title,
            s.directory as session_dir
        FROM message m
        LEFT JOIN session s ON m.session_id = s.id
        WHERE m.data LIKE '%tokens%'
    """)

    messages = []
    for row in cur.fetchall():
        try:
            data = json.loads(row["data"])
            if "tokens" in data:
                tokens = data["tokens"]
                ts = row["time_created"] / 1000
                dt = datetime.fromtimestamp(ts)
                messages.append({
                    "date": dt.strftime("%Y-%m-%d"),
                    "hour": dt.hour,
                    "model": data.get("modelID", "unknown"),
                    "provider": data.get("providerID", "unknown"),
                    "session_title": row["session_title"] or "Untitled",
                    "input_tokens": tokens.get("input", 0),
                    "output_tokens": tokens.get("output", 0),
                    "reasoning_tokens": tokens.get("reasoning", 0),
                    "cache_read": tokens.get("cache", {}).get("read", 0),
                    "cache_write": tokens.get("cache", {}).get("write", 0),
                    "cost": data.get("cost", 0),
                })
        except (json.JSONDecodeError, TypeError):
            continue

    conn.close()
    return messages


def fetch_9router_data():
    """Fetch token usage data from 9Router API."""
    try:
        # Login
        login_data = json.dumps({"password": ROUTER_PASSWORD}).encode("utf-8")
        login_req = urllib.request.Request(
            f"{ROUTER_URL}/api/auth/login",
            data=login_data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        login_resp = urllib.request.urlopen(login_req)
        cookies = login_resp.headers.get_all("Set-Cookie") or []

        # Get session cookie
        cookie_str = "; ".join(c.split(";")[0] for c in cookies) if cookies else ""

        # Fetch usage stats
        stats_req = urllib.request.Request(
            f"{ROUTER_URL}/api/usage/stats?period=60d",
            headers={"Cookie": cookie_str, "Content-Type": "application/json"}
        )
        stats_resp = urllib.request.urlopen(stats_req)
        stats = json.loads(stats_resp.read().decode("utf-8"))

        return stats
    except Exception as e:
        print(f"Warning: Could not fetch 9Router data: {e}")
        return None


def merge_data(mimo_messages, router_stats):
    """Merge MiMo Code and 9Router data into unified format."""
    all_messages = list(mimo_messages)

    # Add 9Router recent requests as messages
    if router_stats and "recentRequests" in router_stats:
        for req in router_stats["recentRequests"]:
            try:
                dt = datetime.fromisoformat(req["timestamp"].replace("Z", "+00:00"))
                all_messages.append({
                    "date": dt.strftime("%Y-%m-%d"),
                    "hour": dt.hour,
                    "model": req.get("model", "unknown"),
                    "provider": req.get("provider", "9router"),
                    "session_title": f"9Router - {req.get('model', 'unknown')}",
                    "input_tokens": req.get("promptTokens", 0),
                    "output_tokens": req.get("completionTokens", 0),
                    "reasoning_tokens": 0,
                    "cache_read": req.get("cachedTokens", 0),
                    "cache_write": 0,
                    "cost": 0,
                })
            except (ValueError, KeyError):
                continue

    # Add 9Router aggregated by-model data
    if router_stats and "byModel" in router_stats:
        for model_key, model_data in router_stats["byModel"].items():
            raw_model = model_data.get("rawModel", model_key)
            provider = model_data.get("provider", "9router")
            # Add aggregated counts (these are already in recentRequests, so skip)
            # The recentRequests are the most recent 20, we'll use those

    return all_messages


def aggregate(messages):
    daily = defaultdict(lambda: {"input": 0, "output": 0, "cache_read": 0, "count": 0, "cost": 0})
    model = defaultdict(lambda: {"input": 0, "output": 0, "cache_read": 0, "count": 0, "cost": 0})
    hourly = defaultdict(lambda: {"count": 0, "tokens": 0})
    provider = defaultdict(lambda: {"input": 0, "output": 0, "count": 0, "cost": 0})
    session = defaultdict(lambda: {"title": "", "count": 0, "input": 0, "output": 0, "cache_read": 0})

    for m in messages:
        d = m["date"]
        daily[d]["input"] += m["input_tokens"]
        daily[d]["output"] += m["output_tokens"]
        daily[d]["cache_read"] += m["cache_read"]
        daily[d]["count"] += 1
        daily[d]["cost"] += m["cost"]

        mod = m["model"]
        model[mod]["input"] += m["input_tokens"]
        model[mod]["output"] += m["output_tokens"]
        model[mod]["cache_read"] += m["cache_read"]
        model[mod]["count"] += 1
        model[mod]["cost"] += m["cost"]

        hourly[m["hour"]]["count"] += 1
        hourly[m["hour"]]["tokens"] += m["input_tokens"] + m["output_tokens"]

        prov = m["provider"]
        provider[prov]["input"] += m["input_tokens"]
        provider[prov]["output"] += m["output_tokens"]
        provider[prov]["count"] += 1
        provider[prov]["cost"] += m["cost"]

        sid = m["session_title"]
        session[sid]["title"] = sid
        session[sid]["count"] += 1
        session[sid]["input"] += m["input_tokens"]
        session[sid]["output"] += m["output_tokens"]
        session[sid]["cache_read"] += m["cache_read"]

    return daily, model, hourly, provider, session


def fmt_tokens(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def generate_html(messages, daily, model, hourly, provider, session, router_stats):
    total_input = sum(m["input_tokens"] for m in messages)
    total_output = sum(m["output_tokens"] for m in messages)
    total_cache_read = sum(m["cache_read"] for m in messages)
    total_cost = sum(m["cost"] for m in messages)
    total_messages = len(messages)
    total_requests = total_messages
    avg_tokens = (total_input + total_output) // total_messages if total_messages else 0

    # Add 9Router aggregated totals (not just recent requests)
    if router_stats:
        total_input += router_stats.get("totalPromptTokens", 0)
        total_output += router_stats.get("totalCompletionTokens", 0)
        total_cache_read += router_stats.get("totalCachedTokens", 0)
        total_cost += router_stats.get("totalCost", 0)
        total_requests += router_stats.get("totalRequests", 0)

    dates = sorted(daily.keys())
    if dates:
        first_date = dates[0]
        last_date = dates[-1]
    else:
        first_date = last_date = "N/A"

    daily_dates = json.dumps(dates)
    daily_input = json.dumps([daily[d]["input"] for d in dates])
    daily_output = json.dumps([daily[d]["output"] for d in dates])
    daily_cache_read = json.dumps([daily[d]["cache_read"] for d in dates])

    model_names_sorted = sorted(model.keys(), key=lambda x: model[x]["input"] + model[x]["output"], reverse=True)
    top_models_bar = model_names_sorted[:8]
    model_bar_labels = json.dumps([n.split("/")[-1] if "/" in n else n for n in top_models_bar])
    model_bar_data = json.dumps([model[n]["input"] + model[n]["output"] for n in top_models_bar])

    top5_models = model_names_sorted[:5]
    other_count = sum(model[n]["count"] for n in model_names_sorted[5:])
    doughnut_labels = json.dumps([n.split("/")[-1] if "/" in n else n for n in top5_models] + ["Other"])
    doughnut_data = json.dumps([model[n]["count"] for n in top5_models] + [other_count])

    hourly_labels = json.dumps([f"{h:02d}:00" for h in range(24)])
    hourly_counts = json.dumps([hourly[h]["count"] for h in range(24)])

    # Provider breakdown for chart
    provider_names_sorted = sorted(provider.keys(), key=lambda x: provider[x]["input"] + provider[x]["output"], reverse=True)
    provider_labels = json.dumps(provider_names_sorted[:8])
    provider_data = json.dumps([provider[p]["input"] + provider[p]["output"] for p in provider_names_sorted[:8]])

    model_rows = ""
    model_colors = ["#00687a", "#57dffe", "#00275b", "#004e5c", "#dce9ff", "#c5c6cd", "#4cd7f6", "#adc6ff"]
    for i, n in enumerate(model_names_sorted):
        m = model[n]
        total = m["input"] + m["output"]
        short_name = n.split("/")[-1] if "/" in n else n
        color = model_colors[i % len(model_colors)]
        bg = "bg-surface" if i % 2 == 1 else ""
        model_rows += f"""
        <tr class="{bg} hover:bg-surface-container-low transition-colors">
            <td class="p-sm pl-md flex items-center gap-xs"><div class="w-2 h-2 rounded-full" style="background:{color}"></div>{short_name}</td>
            <td class="p-sm text-right">{m['count']:,}</td>
            <td class="p-sm text-right">{fmt_tokens(m['input'])}</td>
            <td class="p-sm text-right">{fmt_tokens(m['output'])}</td>
            <td class="p-sm text-right">{fmt_tokens(m['cache_read'])}</td>
            <td class="p-sm text-right pr-md">${m['cost']:.2f}</td>
        </tr>"""

    # Provider table rows
    provider_rows = ""
    provider_colors = ["#00687a", "#57dffe", "#00275b", "#004e5c", "#dce9ff", "#c5c6cd", "#4cd7f6", "#adc6ff"]
    for i, n in enumerate(provider_names_sorted):
        p = provider[n]
        total = p["input"] + p["output"]
        bg = "bg-surface" if i % 2 == 1 else ""
        color = provider_colors[i % len(provider_colors)]
        provider_rows += f"""
        <tr class="{bg} hover:bg-surface-container-low transition-colors">
            <td class="p-sm pl-md flex items-center gap-xs"><div class="w-2 h-2 rounded-full" style="background:{color}"></div>{n}</td>
            <td class="p-sm text-right">{p['count']:,}</td>
            <td class="p-sm text-right">{fmt_tokens(p['input'])}</td>
            <td class="p-sm text-right">{fmt_tokens(p['output'])}</td>
            <td class="p-sm text-right pr-md">${p['cost']:.2f}</td>
        </tr>"""

    top_sessions = sorted(session.values(), key=lambda x: x["input"] + x["output"], reverse=True)[:10]
    session_rows = ""
    for i, s in enumerate(top_sessions):
        total = s["input"] + s["output"]
        title = s["title"][:50] + ("..." if len(s["title"]) > 50 else "")
        bg = "bg-surface" if i % 2 == 1 else ""
        session_rows += f"""
        <tr class="{bg} hover:bg-surface-container-low transition-colors">
            <td class="p-sm pl-md">#{i+1}</td>
            <td class="p-sm text-secondary" title="{s['title']}">{title}</td>
            <td class="p-sm text-right">{s['count']}</td>
            <td class="p-sm text-right">{fmt_tokens(s['input'])}</td>
            <td class="p-sm text-right">{fmt_tokens(s['output'])}</td>
            <td class="p-sm text-right">{fmt_tokens(s['cache_read'])}</td>
            <td class="p-sm text-right pr-md">{fmt_tokens(total)}</td>
        </tr>"""

    date_range = f"{first_date} - {last_date}"

    # 9Router summary
    router_summary = ""
    if router_stats:
        router_summary = f"""
        <div class="bg-surface-container-lowest border border-outline-variant rounded-lg p-md">
            <div class="flex items-center gap-sm mb-sm">
                <span class="material-symbols-outlined text-secondary text-[20px]">router</span>
                <span class="font-mono-label text-mono-label text-on-surface-variant uppercase tracking-wider">9Router Summary</span>
            </div>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-sm">
                <div><span class="text-on-surface-variant text-xs">Requests</span><div class="font-stat-value text-stat-value text-primary">{router_stats.get('totalRequests', 0):,}</div></div>
                <div><span class="text-on-surface-variant text-xs">Input Tokens</span><div class="font-stat-value text-stat-value text-primary">{fmt_tokens(router_stats.get('totalPromptTokens', 0))}</div></div>
                <div><span class="text-on-surface-variant text-xs">Output Tokens</span><div class="font-stat-value text-stat-value text-primary">{fmt_tokens(router_stats.get('totalCompletionTokens', 0))}</div></div>
                <div><span class="text-on-surface-variant text-xs">Cost</span><div class="font-stat-value text-stat-value text-primary">${router_stats.get('totalCost', 0):.2f}</div></div>
            </div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>TokenPulse - Token Usage Dashboard</title>
<script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<link href="https://fonts.googleapis.com" rel="preconnect"/>
<link crossorigin="" href="https://fonts.gstatic.com" rel="preconnect"/>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;600;700&family=Plus+Jakarta+Sans:wght@400;600;700&display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap" rel="stylesheet"/>
<script>
tailwind.config = {{
  darkMode: "class",
  theme: {{
    extend: {{
      colors: {{
        "outline-variant": "#c5c6cd",
        "primary-fixed": "#d8e3fb",
        "outline": "#75777d",
        "on-background": "#0b1c30",
        "primary": "#091426",
        "background": "#f8f9ff",
        "on-primary-container": "#8590a6",
        "on-secondary-container": "#006172",
        "tertiary": "#001334",
        "surface-container-high": "#dce9ff",
        "on-surface-variant": "#45474c",
        "surface-variant": "#d3e4fe",
        "surface-tint": "#545f73",
        "surface-container-highest": "#d3e4fe",
        "secondary": "#00687a",
        "inverse-on-surface": "#eaf1ff",
        "on-tertiary-fixed": "#001a42",
        "surface-container-lowest": "#ffffff",
        "primary-fixed-dim": "#bcc7de",
        "on-tertiary": "#ffffff",
        "on-tertiary-container": "#4c8dff",
        "on-secondary": "#ffffff",
        "error-container": "#ffdad6",
        "on-tertiary-fixed-variant": "#004395",
        "on-error-container": "#93000a",
        "on-secondary-fixed": "#001f26",
        "tertiary-fixed-dim": "#adc6ff",
        "tertiary-fixed": "#d8e2ff",
        "primary-container": "#1e293b",
        "on-surface": "#0b1c30",
        "surface": "#f8f9ff",
        "tertiary-container": "#00275b",
        "surface-dim": "#cbdbf5",
        "surface-bright": "#f8f9ff",
        "inverse-surface": "#213145",
        "on-secondary-fixed-variant": "#004e5c",
        "on-error": "#ffffff",
        "error": "#ba1a1a",
        "secondary-container": "#57dffe",
        "on-primary-fixed": "#111c2d",
        "surface-container": "#e5eeff",
        "surface-container-low": "#eff4ff",
        "secondary-fixed": "#acedff",
        "secondary-fixed-dim": "#4cd7f6",
        "inverse-primary": "#bcc7de",
        "on-primary": "#ffffff"
      }},
      borderRadius: {{
        DEFAULT: "0.125rem",
        lg: "0.25rem",
        xl: "0.5rem",
        full: "0.75rem"
      }},
      spacing: {{
        lg: "24px",
        margin: "24px",
        md: "16px",
        sm: "8px",
        gutter: "20px",
        xl: "32px",
        base: "4px",
        xs: "4px"
      }},
      fontFamily: {{
        "body-md": ["Plus Jakarta Sans"],
        "mono-data": ["JetBrains Mono"],
        "stat-value": ["JetBrains Mono"],
        "mono-label": ["JetBrains Mono"],
        "display-lg": ["Plus Jakarta Sans"],
        "body-sm": ["Plus Jakarta Sans"],
        "headline-md": ["Plus Jakarta Sans"]
      }},
      fontSize: {{
        "body-md": ["16px", {{ lineHeight: "24px", fontWeight: "400" }}],
        "mono-data": ["14px", {{ lineHeight: "20px", fontWeight: "500" }}],
        "stat-value": ["24px", {{ lineHeight: "32px", letterSpacing: "-0.03em", fontWeight: "700" }}],
        "mono-label": ["12px", {{ lineHeight: "16px", fontWeight: "600" }}],
        "display-lg": ["36px", {{ lineHeight: "44px", letterSpacing: "-0.02em", fontWeight: "700" }}],
        "body-sm": ["14px", {{ lineHeight: "20px", fontWeight: "400" }}],
        "headline-md": ["24px", {{ lineHeight: "32px", letterSpacing: "-0.01em", fontWeight: "600" }}]
      }}
    }},
  }},
}}
</script>
<style>
body {{ background-color: #F8FAFC; }}
.dark body, body.dark {{
  background-color: #0f172a !important;
  color: #e2e8f0 !important;
}}
.dark .bg-surface-container-lowest, body.dark .bg-surface-container-lowest {{
  background-color: #1e293b !important;
  border-color: #334155 !important;
}}
.dark .bg-surface-container-low, body.dark .bg-surface-container-low {{
  background-color: #1e293b !important;
}}
.dark .bg-surface, body.dark .bg-surface {{
  background-color: #0f172a !important;
}}
.dark .bg-surface-container, body.dark .bg-surface-container {{
  background-color: #1e293b !important;
}}
.dark .bg-surface-container-high, body.dark .bg-surface-container-high {{
  background-color: #334155 !important;
}}
.dark .bg-surface-container-highest, body.dark .bg-surface-container-highest {{
  background-color: #475569 !important;
}}
.dark .text-primary, body.dark .text-primary {{
  color: #e2e8f0 !important;
}}
.dark .text-on-surface, body.dark .text-on-surface {{
  color: #e2e8f0 !important;
}}
.dark .text-on-surface-variant, body.dark .text-on-surface-variant {{
  color: #94a3b8 !important;
}}
.dark .text-on-secondary-container, body.dark .text-on-secondary-container {{
  color: #e2e8f0 !important;
}}
.dark .border-outline-variant, body.dark .border-outline-variant {{
  border-color: #334155 !important;
}}
.dark .bg-secondary-container, body.dark .bg-secondary-container {{
  background-color: #0e7490 !important;
}}
.dark .bg-background, body.dark .bg-background {{
  background-color: #0f172a !important;
}}
/* Chart dark mode fixes */
.dark canvas, body.dark canvas {{
  background-color: transparent !important;
}}
/* Table dark mode */
.dark tbody tr, body.dark tbody tr {{
  border-color: #334155 !important;
}}
.dark tbody tr:nth-child(even), body.dark tbody tr:nth-child(even) {{
  background-color: #1e293b !important;
}}
.dark tbody tr:hover, body.dark tbody tr:hover {{
  background-color: #334155 !important;
}}
.dark th, body.dark th {{
  background-color: #1e293b !important;
  border-color: #334155 !important;
  color: #94a3b8 !important;
}}
.dark td, body.dark td {{
  border-color: #334155 !important;
  color: #e2e8f0 !important;
}}
.dark .text-secondary, body.dark .text-secondary {{
  color: #22d3ee !important;
}}
</style>
</head>
<body class="font-body-md text-body-md text-on-surface bg-background flex h-screen overflow-hidden">

<!-- SideNavBar -->
<nav id="sidebar" class="bg-surface-container-lowest text-primary h-screen w-64 border-r border-outline-variant flex-shrink-0 flex flex-col z-50 relative hidden md:flex transition-all duration-300">
<div class="flex flex-col h-full p-lg gap-md">
  <div class="mb-lg">
    <div class="flex items-center gap-sm mb-xs">
      <div class="w-8 h-8 rounded bg-secondary flex items-center justify-center">
        <span class="material-symbols-outlined text-on-secondary text-[20px]" style="font-variation-settings: 'FILL' 1;">monitoring</span>
      </div>
      <h1 class="text-headline-md font-headline-md text-primary truncate">TokenPulse</h1>
    </div>
    <div class="text-body-sm font-body-sm text-on-surface-variant">v1.0.0</div>
  </div>
  <div class="flex flex-col gap-xs flex-grow">
    <a onclick="switchTab('overview')" class="nav-link active flex items-center gap-sm px-md py-sm bg-secondary-container text-on-secondary-container rounded-xl opacity-90 cursor-pointer" data-tab="overview">
      <span class="material-symbols-outlined" style="font-variation-settings: 'FILL' 1;">dashboard</span>
      <span class="font-body-md">Overview</span>
    </a>
    <a onclick="switchTab('models')" class="nav-link flex items-center gap-sm px-md py-sm text-on-surface-variant hover:bg-surface-container-high rounded-xl transition-colors duration-200 cursor-pointer" data-tab="models">
      <span class="material-symbols-outlined">extension</span>
      <span class="font-body-md">Models</span>
    </a>
    <a onclick="switchTab('providers')" class="nav-link flex items-center gap-sm px-md py-sm text-on-surface-variant hover:bg-surface-container-high rounded-xl transition-colors duration-200 cursor-pointer" data-tab="providers">
      <span class="material-symbols-outlined">smart_toy</span>
      <span class="font-body-md">Providers</span>
    </a>
    <a onclick="switchTab('sessions')" class="nav-link flex items-center gap-sm px-md py-sm text-on-surface-variant hover:bg-surface-container-high rounded-xl transition-colors duration-200 cursor-pointer" data-tab="sessions">
      <span class="material-symbols-outlined">history</span>
      <span class="font-body-md">Sessions</span>
    </a>
  </div>
</div>
</nav>

<!-- Main Content Area -->
<div class="flex-grow flex flex-col h-screen overflow-hidden">

<!-- TopNavBar -->
<header class="bg-surface text-primary border-b border-outline-variant flex justify-between items-center w-full px-lg h-16 sticky top-0 z-40 flex-shrink-0">
  <div class="flex items-center gap-md">
    <button onclick="toggleSidebar()" class="text-on-surface-variant hover:text-primary transition-colors">
      <span class="material-symbols-outlined">menu</span>
    </button>
    <div class="font-headline-md text-headline-md font-bold text-primary">TokenPulse Dashboard</div>
  </div>
  <div class="flex items-center gap-md">
    <div class="hidden md:flex items-center text-body-sm font-mono-data text-on-surface-variant bg-surface-container-low px-sm py-xs rounded border border-outline-variant">
      <span class="material-symbols-outlined text-[16px] mr-xs">calendar_today</span>
      {date_range}
    </div>
    <div id="refresh-status" class="hidden md:flex items-center text-body-sm font-mono-data text-secondary bg-surface-container-low px-sm py-xs rounded border border-outline-variant">
      <span class="material-symbols-outlined text-[16px] mr-xs animate-spin">refresh</span>
      <span>Refreshing...</span>
    </div>
    <div class="flex items-center gap-xs">
      <button onclick="refreshData()" class="p-xs text-on-surface-variant hover:text-primary transition-colors rounded-full hover:bg-surface-container-low" title="Refresh data">
        <span class="material-symbols-outlined">refresh</span>
      </button>
      <button onclick="toggleDarkMode()" class="p-xs text-on-surface-variant hover:text-primary transition-colors rounded-full hover:bg-surface-container-low" title="Toggle dark mode">
        <span id="theme-icon" class="material-symbols-outlined">dark_mode</span>
      </button>
    </div>
  </div>
</header>

<!-- Scrollable Dashboard Content -->
<main class="flex-grow overflow-y-auto p-lg md:p-margin bg-background">
<div class="max-w-[1440px] mx-auto flex flex-col gap-margin">

<!-- Overview Tab -->
<div id="tab-overview" class="tab-content">
<!-- Stat Cards Grid -->
<div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-md">
  <div class="bg-surface-container-lowest border border-outline-variant rounded-lg p-md flex flex-col justify-between">
    <div class="flex justify-between items-start">
      <span class="font-mono-label text-mono-label text-on-surface-variant uppercase tracking-wider">Input Tokens</span>
      <span class="material-symbols-outlined text-on-surface-variant text-[20px]">input</span>
    </div>
    <div class="mt-sm">
      <span class="font-stat-value text-stat-value text-primary">{fmt_tokens(total_input)}</span>
    </div>
  </div>
  <div class="bg-surface-container-lowest border border-outline-variant rounded-lg p-md flex flex-col justify-between">
    <div class="flex justify-between items-start">
      <span class="font-mono-label text-mono-label text-on-surface-variant uppercase tracking-wider">Output Tokens</span>
      <span class="material-symbols-outlined text-on-surface-variant text-[20px]">output</span>
    </div>
    <div class="mt-sm">
      <span class="font-stat-value text-stat-value text-primary">{fmt_tokens(total_output)}</span>
    </div>
  </div>
  <div class="bg-surface-container-lowest border border-outline-variant rounded-lg p-md flex flex-col justify-between">
    <div class="flex justify-between items-start">
      <span class="font-mono-label text-mono-label text-on-surface-variant uppercase tracking-wider">Cache Read</span>
      <span class="material-symbols-outlined text-on-surface-variant text-[20px]">storage</span>
    </div>
    <div class="mt-sm">
      <span class="font-stat-value text-stat-value text-primary">{fmt_tokens(total_cache_read)}</span>
    </div>
  </div>
  <div class="bg-surface-container-lowest border border-outline-variant rounded-lg p-md flex flex-col justify-between">
    <div class="flex justify-between items-start">
      <span class="font-mono-label text-mono-label text-on-surface-variant uppercase tracking-wider">Messages</span>
      <span class="material-symbols-outlined text-on-surface-variant text-[20px]">forum</span>
    </div>
    <div class="mt-sm">
      <span class="font-stat-value text-stat-value text-primary">{total_messages:,}</span>
    </div>
  </div>
  <div class="bg-surface-container-lowest border border-outline-variant rounded-lg p-md flex flex-col justify-between">
    <div class="flex justify-between items-start">
      <span class="font-mono-label text-mono-label text-on-surface-variant uppercase tracking-wider">Total Requests</span>
      <span class="material-symbols-outlined text-on-surface-variant text-[20px]">api</span>
    </div>
    <div class="mt-sm">
      <span class="font-stat-value text-stat-value text-primary">{total_requests:,}</span>
    </div>
  </div>
  <div class="bg-surface-container-lowest border border-outline-variant rounded-lg p-md flex flex-col justify-between">
    <div class="flex justify-between items-start">
      <span class="font-mono-label text-mono-label text-on-surface-variant uppercase tracking-wider">Total Cost</span>
      <span class="material-symbols-outlined text-on-surface-variant text-[20px]">payments</span>
    </div>
    <div class="mt-sm">
      <span class="font-stat-value text-stat-value text-primary">${total_cost:.2f}</span>
    </div>
  </div>
  <div class="bg-surface-container-lowest border border-outline-variant rounded-lg p-md flex flex-col justify-between">
    <div class="flex justify-between items-start">
      <span class="font-mono-label text-mono-label text-on-surface-variant uppercase tracking-wider">Avg Tokens/Msg</span>
      <span class="material-symbols-outlined text-on-surface-variant text-[20px]">analytics</span>
    </div>
    <div class="mt-sm">
      <span class="font-stat-value text-stat-value text-primary">{fmt_tokens(avg_tokens)}</span>
    </div>
  </div>
</div>

<!-- 9Router Summary -->
{router_summary}

<!-- Charts Bento Grid -->
<div class="grid grid-cols-1 lg:grid-cols-12 gap-gutter">
  <div class="lg:col-span-8 bg-surface-container-lowest border border-outline-variant rounded-lg p-md flex flex-col min-h-[300px]">
    <h3 class="font-mono-label text-mono-label text-on-surface-variant uppercase tracking-wider mb-md">Daily Usage</h3>
    <div class="flex-grow relative">
      <canvas id="dailyChart"></canvas>
    </div>
  </div>
  <div class="lg:col-span-4 bg-surface-container-lowest border border-outline-variant rounded-lg p-md flex flex-col min-h-[300px]">
    <h3 class="font-mono-label text-mono-label text-on-surface-variant uppercase tracking-wider mb-md">Messages by Model</h3>
    <div class="flex-grow relative">
      <canvas id="modelCountChart"></canvas>
    </div>
  </div>
  <div class="lg:col-span-4 bg-surface-container-lowest border border-outline-variant rounded-lg p-md flex flex-col h-[250px]">
    <h3 class="font-mono-label text-mono-label text-on-surface-variant uppercase tracking-wider mb-md">Token by Model</h3>
    <div class="flex-grow relative">
      <canvas id="modelChart"></canvas>
    </div>
  </div>
  <div class="lg:col-span-8 bg-surface-container-lowest border border-outline-variant rounded-lg p-md flex flex-col h-[250px]">
    <h3 class="font-mono-label text-mono-label text-on-surface-variant uppercase tracking-wider mb-md">Hourly Activity</h3>
    <div class="flex-grow relative">
      <canvas id="hourlyChart"></canvas>
    </div>
  </div>
</div>

<!-- Tables Section -->
<div class="flex flex-col gap-margin">
  <div class="bg-surface-container-lowest border border-outline-variant rounded-lg overflow-hidden">
    <div class="p-md border-b border-outline-variant">
      <h3 class="font-headline-md text-headline-md font-bold text-primary">Model Breakdown</h3>
    </div>
    <div class="overflow-x-auto">
      <table class="w-full text-left border-collapse">
        <thead class="bg-surface-container-low font-mono-label text-mono-label text-on-surface-variant uppercase border-b border-outline-variant">
          <tr>
            <th class="p-sm pl-md">Model</th>
            <th class="p-sm text-right">Messages</th>
            <th class="p-sm text-right">Input</th>
            <th class="p-sm text-right">Output</th>
            <th class="p-sm text-right">Cache Read</th>
            <th class="p-sm text-right pr-md">Cost</th>
          </tr>
        </thead>
        <tbody class="font-mono-data text-mono-data text-primary divide-y divide-outline-variant">
          {model_rows}
        </tbody>
      </table>
    </div>
  </div>
  <div class="bg-surface-container-lowest border border-outline-variant rounded-lg overflow-hidden">
    <div class="p-md border-b border-outline-variant">
      <h3 class="font-headline-md text-headline-md font-bold text-primary">Top Sessions</h3>
    </div>
    <div class="overflow-x-auto">
      <table class="w-full text-left border-collapse">
        <thead class="bg-surface-container-low font-mono-label text-mono-label text-on-surface-variant uppercase border-b border-outline-variant">
          <tr>
            <th class="p-sm pl-md">Rank</th>
            <th class="p-sm">Session</th>
            <th class="p-sm text-right">Messages</th>
            <th class="p-sm text-right">Input</th>
            <th class="p-sm text-right">Output</th>
            <th class="p-sm text-right">Cache Read</th>
            <th class="p-sm text-right pr-md">Total</th>
          </tr>
        </thead>
        <tbody class="font-mono-data text-mono-data text-primary divide-y divide-outline-variant">
          {session_rows}
        </tbody>
      </table>
    </div>
  </div>
</div>
</div> <!-- End Overview Tab -->

<!-- Models Tab -->
<div id="tab-models" class="tab-content hidden">
  <div class="bg-surface-container-lowest border border-outline-variant rounded-lg overflow-hidden">
    <div class="p-md border-b border-outline-variant">
      <h3 class="font-headline-md text-headline-md font-bold text-primary">All Models</h3>
    </div>
    <div class="overflow-x-auto">
      <table class="w-full text-left border-collapse">
        <thead class="bg-surface-container-low font-mono-label text-mono-label text-on-surface-variant uppercase border-b border-outline-variant">
          <tr>
            <th class="p-sm pl-md">Model</th>
            <th class="p-sm text-right">Messages</th>
            <th class="p-sm text-right">Input</th>
            <th class="p-sm text-right">Output</th>
            <th class="p-sm text-right">Cache Read</th>
            <th class="p-sm text-right">Total</th>
            <th class="p-sm text-right pr-md">Cost</th>
          </tr>
        </thead>
        <tbody class="font-mono-data text-mono-data text-primary divide-y divide-outline-variant">
          {model_rows}
        </tbody>
      </table>
    </div>
  </div>
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-gutter mt-margin">
    <div class="bg-surface-container-lowest border border-outline-variant rounded-lg p-md flex flex-col h-[350px]">
      <h3 class="font-mono-label text-mono-label text-on-surface-variant uppercase tracking-wider mb-md">Token Distribution by Model</h3>
      <div class="flex-grow relative">
        <canvas id="modelDetailChart"></canvas>
      </div>
    </div>
    <div class="bg-surface-container-lowest border border-outline-variant rounded-lg p-md flex flex-col h-[350px]">
      <h3 class="font-mono-label text-mono-label text-on-surface-variant uppercase tracking-wider mb-md">Message Count by Model</h3>
      <div class="flex-grow relative">
        <canvas id="modelMessageChart"></canvas>
      </div>
    </div>
  </div>
</div>

<!-- Providers Tab -->
<div id="tab-providers" class="tab-content hidden">
  <div class="bg-surface-container-lowest border border-outline-variant rounded-lg overflow-hidden">
    <div class="p-md border-b border-outline-variant">
      <h3 class="font-headline-md text-headline-md font-bold text-primary">All Providers</h3>
    </div>
    <div class="overflow-x-auto">
      <table class="w-full text-left border-collapse">
        <thead class="bg-surface-container-low font-mono-label text-mono-label text-on-surface-variant uppercase border-b border-outline-variant">
          <tr>
            <th class="p-sm pl-md">Provider</th>
            <th class="p-sm text-right">Requests</th>
            <th class="p-sm text-right">Input</th>
            <th class="p-sm text-right">Output</th>
            <th class="p-sm text-right pr-md">Cost</th>
          </tr>
        </thead>
        <tbody class="font-mono-data text-mono-data text-primary divide-y divide-outline-variant">
          {provider_rows}
        </tbody>
      </table>
    </div>
  </div>
  <div class="mt-margin bg-surface-container-lowest border border-outline-variant rounded-lg p-md flex flex-col h-[350px]">
    <h3 class="font-mono-label text-mono-label text-on-surface-variant uppercase tracking-wider mb-md">Token Usage by Provider</h3>
    <div class="flex-grow relative">
      <canvas id="providerChart"></canvas>
    </div>
  </div>
</div>

<!-- Sessions Tab -->
<div id="tab-sessions" class="tab-content hidden">
  <div class="bg-surface-container-lowest border border-outline-variant rounded-lg overflow-hidden">
    <div class="p-md border-b border-outline-variant">
      <h3 class="font-headline-md text-headline-md font-bold text-primary">All Sessions</h3>
    </div>
    <div class="overflow-x-auto">
      <table class="w-full text-left border-collapse">
        <thead class="bg-surface-container-low font-mono-label text-mono-label text-on-surface-variant uppercase border-b border-outline-variant">
          <tr>
            <th class="p-sm pl-md">Rank</th>
            <th class="p-sm">Session</th>
            <th class="p-sm text-right">Messages</th>
            <th class="p-sm text-right">Input</th>
            <th class="p-sm text-right">Output</th>
            <th class="p-sm text-right">Cache Read</th>
            <th class="p-sm text-right pr-md">Total</th>
          </tr>
        </thead>
        <tbody class="font-mono-data text-mono-data text-primary divide-y divide-outline-variant">
          {session_rows}
        </tbody>
      </table>
    </div>
  </div>
</div>

</div>

<footer class="bg-surface-container-lowest text-on-surface-variant font-mono-label text-mono-label border-t border-outline-variant flex justify-between items-center w-full px-lg py-md mt-margin">
  <div>&copy; 2026 TokenPulse. Data from MiMo Code + 9Router | Last updated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>
  <div class="flex gap-md">
    <span class="text-secondary cursor-pointer hover:text-primary transition-colors">Docs</span>
    <span class="text-secondary cursor-pointer hover:text-primary transition-colors">GitHub</span>
  </div>
</footer>

</div>
</div>

<script>
// Tab switching
function switchTab(tabName) {{
  // Hide all tab content
  document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
  // Show selected tab
  document.getElementById('tab-' + tabName).classList.remove('hidden');
  // Update nav links
  document.querySelectorAll('.nav-link').forEach(el => {{
    el.classList.remove('bg-secondary-container', 'text-on-secondary-container', 'opacity-90');
    el.classList.add('text-on-surface-variant');
    const icon = el.querySelector('.material-symbols-outlined');
    if (icon) icon.style.fontVariationSettings = "'FILL' 0";
  }});
  const activeLink = document.querySelector(`[data-tab="${{tabName}}"]`);
  if (activeLink) {{
    activeLink.classList.add('bg-secondary-container', 'text-on-secondary-container', 'opacity-90');
    activeLink.classList.remove('text-on-surface-variant');
    const icon = activeLink.querySelector('.material-symbols-outlined');
    if (icon) icon.style.fontVariationSettings = "'FILL' 1";
  }}
  // Resize charts in the active tab
  setTimeout(() => window.dispatchEvent(new Event('resize')), 100);
}}

// Sidebar toggle
function toggleSidebar() {{
  const sidebar = document.getElementById('sidebar');
  const mainContent = document.querySelector('.main-content');
  sidebar.classList.toggle('hidden');
  sidebar.classList.toggle('md:hidden');
  sidebar.classList.toggle('md:block');
  sidebar.classList.toggle('fixed');
  sidebar.classList.toggle('top-16');
  sidebar.classList.toggle('left-0');
  sidebar.classList.toggle('h-[calc(100vh-4rem)]');
  // Save preference
  const isHidden = sidebar.classList.contains('hidden');
  localStorage.setItem('sidebarHidden', isHidden);
}}

// Load sidebar preference
(function() {{
  const isHidden = localStorage.getItem('sidebarHidden') === 'true';
  if (isHidden) {{
    const sidebar = document.getElementById('sidebar');
    sidebar.classList.add('hidden');
    sidebar.classList.remove('md:flex');
  }}
}})();

// Dark mode
function toggleDarkMode() {{
  document.documentElement.classList.toggle('dark');
  document.body.classList.toggle('dark');
  const isDark = document.documentElement.classList.contains('dark');
  localStorage.setItem('theme', isDark ? 'dark' : 'light');
  const icon = document.getElementById('theme-icon');
  icon.textContent = isDark ? 'light_mode' : 'dark_mode';
  // Update chart colors
  updateChartColors();
  setTimeout(() => window.dispatchEvent(new Event('resize')), 100);
}}

// Load saved theme
(function() {{
  const savedTheme = localStorage.getItem('theme');
  if (savedTheme === 'dark') {{
    document.documentElement.classList.add('dark');
    document.body.classList.add('dark');
    const icon = document.getElementById('theme-icon');
    if (icon) icon.textContent = 'light_mode';
  }}
}})();

// Auto-refresh configuration
const AUTO_REFRESH_INTERVAL = 5 * 60 * 1000; // 5 minutes
let refreshTimer = null;

function refreshData() {{
  const status = document.getElementById('refresh-status');
  status.classList.remove('hidden');
  status.classList.add('flex');
  // Reload page after short delay to show the status
  setTimeout(() => location.reload(), 500);
}}

function startAutoRefresh() {{
  refreshTimer = setInterval(() => {{
    refreshData();
  }}, AUTO_REFRESH_INTERVAL);
}}

// Start auto-refresh on page load
document.addEventListener('DOMContentLoaded', () => {{
  startAutoRefresh();
  // Show last updated time
  console.log('TokenPulse auto-refresh enabled: every 5 minutes');
}});

const fmtTokens = (n) => {{
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return n.toString();
}};

Chart.defaults.color = '#45474c';
Chart.defaults.borderColor = '#e5eeff';
Chart.defaults.font.family = "'JetBrains Mono', monospace";
Chart.defaults.font.size = 11;

// Update chart colors for dark mode
function updateChartColors() {{
  const isDark = document.documentElement.classList.contains('dark');
  Chart.defaults.color = isDark ? '#94a3b8' : '#45474c';
  Chart.defaults.borderColor = isDark ? '#334155' : '#e5eeff';
  // Update all existing charts
  Chart.instances && Object.values(Chart.instances).forEach(chart => {{
    chart.options.scales && Object.values(chart.options.scales).forEach(scale => {{
      if (scale.ticks) scale.ticks.color = isDark ? '#94a3b8' : '#45474c';
      if (scale.grid) scale.grid.color = isDark ? '#334155' : '#e5eeff';
    }});
    chart.update();
  }});
}}

// Initial chart color update
updateChartColors();

const isDarkMode = document.documentElement.classList.contains('dark');

new Chart(document.getElementById('dailyChart'), {{
  type: 'line',
  data: {{
    labels: {daily_dates},
    datasets: [
      {{ label: 'Input', data: {daily_input}, borderColor: isDarkMode ? '#22d3ee' : '#00687a', backgroundColor: isDarkMode ? 'rgba(34,211,238,0.15)' : 'rgba(0,104,122,0.08)', fill: true, tension: 0.3, pointRadius: 2, borderWidth: 2 }},
      {{ label: 'Output', data: {daily_output}, borderColor: isDarkMode ? '#a5f3fc' : '#57dffe', backgroundColor: isDarkMode ? 'rgba(165,243,252,0.15)' : 'rgba(87,223,254,0.08)', fill: true, tension: 0.3, pointRadius: 2, borderWidth: 2 }},
      {{ label: 'Cache Read', data: {daily_cache_read}, borderColor: isDarkMode ? '#818cf8' : '#00275b', backgroundColor: isDarkMode ? 'rgba(129,140,248,0.15)' : 'rgba(0,39,91,0.08)', fill: true, tension: 0.3, pointRadius: 2, borderWidth: 2 }},
    ]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    interaction: {{ mode: 'index', intersect: false }},
    plugins: {{ legend: {{ position: 'top', labels: {{ usePointStyle: true, pointStyle: 'circle', padding: 16 }} }} }},
    scales: {{
      x: {{ grid: {{ display: false }}, ticks: {{ maxRotation: 45, autoSkip: true, maxTicksLimit: 12 }} }},
      y: {{ ticks: {{ callback: v => fmtTokens(v) }} }}
    }}
  }}
}});

new Chart(document.getElementById('modelCountChart'), {{
  type: 'doughnut',
  data: {{
    labels: {doughnut_labels},
    datasets: [{{ data: {doughnut_data}, backgroundColor: isDarkMode ? ['#22d3ee','#a5f3fc','#818cf8','#67e8f9','#06b6d4','#475569'] : ['#00687a','#57dffe','#00275b','#dce9ff','#004e5c','#c5c6cd'], borderWidth: 2, borderColor: isDarkMode ? '#1e293b' : '#ffffff' }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    cutout: '60%',
    plugins: {{ legend: {{ position: 'bottom', labels: {{ usePointStyle: true, pointStyle: 'circle', padding: 12, font: {{ size: 11 }} }} }} }}
  }}
}});

new Chart(document.getElementById('modelChart'), {{
  type: 'bar',
  data: {{
    labels: {model_bar_labels},
    datasets: [{{ label: 'Tokens', data: {model_bar_data}, backgroundColor: isDarkMode ? '#22d3ee' : '#00687a', borderRadius: 4, barThickness: 18 }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    indexAxis: 'y',
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ ticks: {{ callback: v => fmtTokens(v) }}, grid: {{ color: isDarkMode ? '#334155' : '#e5eeff' }} }},
      y: {{ grid: {{ display: false }} }}
    }}
  }}
}});

new Chart(document.getElementById('hourlyChart'), {{
  type: 'bar',
  data: {{
    labels: {hourly_labels},
    datasets: [{{ label: 'Messages', data: {hourly_counts}, backgroundColor: isDarkMode ? '#22d3ee' : '#00687a', borderRadius: 4, barPercentage: 0.7 }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ grid: {{ display: false }} }},
      y: {{ ticks: {{ callback: v => fmtTokens(v) }}, grid: {{ color: '#e5eeff' }} }}
    }}
  }}
}});

// Model Detail Chart (Horizontal Bar - Input vs Output)
new Chart(document.getElementById('modelDetailChart'), {{
  type: 'bar',
  data: {{
    labels: {model_bar_labels},
    datasets: [
      {{ label: 'Input', data: {json.dumps([model[n]["input"] for n in top_models_bar])}, backgroundColor: isDarkMode ? '#22d3ee' : '#00687a', borderRadius: 4 }},
      {{ label: 'Output', data: {json.dumps([model[n]["output"] for n in top_models_bar])}, backgroundColor: isDarkMode ? '#a5f3fc' : '#57dffe', borderRadius: 4 }}
    ]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    indexAxis: 'y',
    plugins: {{ legend: {{ position: 'top', labels: {{ usePointStyle: true, pointStyle: 'circle', padding: 12 }} }} }},
    scales: {{
      x: {{ stacked: true, ticks: {{ callback: v => fmtTokens(v) }}, grid: {{ color: isDarkMode ? '#334155' : '#e5eeff' }} }},
      y: {{ stacked: true, grid: {{ display: false }} }}
    }}
  }}
}});

// Model Message Count Chart (Doughnut)
new Chart(document.getElementById('modelMessageChart'), {{
  type: 'doughnut',
  data: {{
    labels: {doughnut_labels},
    datasets: [{{ data: {doughnut_data}, backgroundColor: isDarkMode ? ['#22d3ee','#a5f3fc','#818cf8','#67e8f9','#06b6d4','#475569'] : ['#00687a','#57dffe','#00275b','#dce9ff','#004e5c','#c5c6cd'], borderWidth: 2, borderColor: isDarkMode ? '#1e293b' : '#ffffff' }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    cutout: '55%',
    plugins: {{ legend: {{ position: 'right', labels: {{ usePointStyle: true, pointStyle: 'circle', padding: 10, font: {{ size: 11 }} }} }} }}
  }}
}});

// Provider Chart (Horizontal Bar)
new Chart(document.getElementById('providerChart'), {{
  type: 'bar',
  data: {{
    labels: {provider_labels},
    datasets: [
      {{ label: 'Input', data: {json.dumps([provider[p]["input"] for p in provider_names_sorted[:8]])}, backgroundColor: isDarkMode ? '#22d3ee' : '#00687a', borderRadius: 4 }},
      {{ label: 'Output', data: {json.dumps([provider[p]["output"] for p in provider_names_sorted[:8]])}, backgroundColor: isDarkMode ? '#a5f3fc' : '#57dffe', borderRadius: 4 }}
    ]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    indexAxis: 'y',
    plugins: {{ legend: {{ position: 'top', labels: {{ usePointStyle: true, pointStyle: 'circle', padding: 12 }} }} }},
    scales: {{
      x: {{ stacked: true, ticks: {{ callback: v => fmtTokens(v) }}, grid: {{ color: isDarkMode ? '#334155' : '#e5eeff' }} }},
      y: {{ stacked: true, grid: {{ display: false }} }}
    }}
  }}
}});
</script>

</body>
</html>"""
    return html


def main():
    print("Fetching MiMo Code data from database...")
    mimo_messages = fetch_mimo_data(DB_PATH)
    print(f"Found {len(mimo_messages)} messages from MiMo Code")

    print("Fetching 9Router data from API...")
    router_stats = fetch_9router_data()
    if router_stats:
        print(f"9Router: {router_stats.get('totalRequests', 0)} requests, {router_stats.get('totalPromptTokens', 0):,} input tokens")
    else:
        print("9Router data not available")

    print("Merging data...")
    all_messages = merge_data(mimo_messages, router_stats)
    print(f"Total messages: {len(all_messages)}")

    print("Aggregating data...")
    daily, model, hourly, provider, session = aggregate(all_messages)

    print("Generating HTML dashboard...")
    html = generate_html(all_messages, daily, model, hourly, provider, session, router_stats)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Dashboard saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()