#!/usr/bin/env python3
"""Generate a MiMo Code Token Usage Dashboard as a self-contained HTML file."""

import sqlite3
import json
import os
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = os.path.expanduser("~/.local/share/mimocode/mimocode.db")
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mimo-token-dashboard.html")


def fetch_data(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Get all messages with token data
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
                ts = row["time_created"] / 1000  # ms to seconds
                dt = datetime.fromtimestamp(ts)
                messages.append({
                    "date": dt.strftime("%Y-%m-%d"),
                    "hour": dt.hour,
                    "day_of_week": dt.strftime("%A"),
                    "model": data.get("modelID", "unknown"),
                    "provider": data.get("providerID", "unknown"),
                    "agent": row["agent_id"],
                    "session_title": row["session_title"] or "Untitled",
                    "session_dir": row["session_dir"] or "",
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


def aggregate(messages):
    # Daily aggregation
    daily = defaultdict(lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "reasoning": 0, "count": 0, "cost": 0})
    # Model aggregation
    model = defaultdict(lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "count": 0, "cost": 0})
    # Hourly aggregation
    hourly = defaultdict(lambda: {"count": 0, "tokens": 0})
    # Agent aggregation
    agent = defaultdict(lambda: {"count": 0, "tokens": 0})
    # Session aggregation
    session = defaultdict(lambda: {"title": "", "count": 0, "input": 0, "output": 0, "cache_read": 0})
    # Day of week
    dow = defaultdict(lambda: {"count": 0, "tokens": 0})
    # Model by day
    model_daily = defaultdict(lambda: defaultdict(lambda: {"input": 0, "output": 0}))

    for m in messages:
        d = m["date"]
        daily[d]["input"] += m["input_tokens"]
        daily[d]["output"] += m["output_tokens"]
        daily[d]["cache_read"] += m["cache_read"]
        daily[d]["cache_write"] += m["cache_write"]
        daily[d]["reasoning"] += m["reasoning_tokens"]
        daily[d]["count"] += 1
        daily[d]["cost"] += m["cost"]

        mod = m["model"]
        model[mod]["input"] += m["input_tokens"]
        model[mod]["output"] += m["output_tokens"]
        model[mod]["cache_read"] += m["cache_read"]
        model[mod]["cache_write"] += m["cache_write"]
        model[mod]["count"] += 1
        model[mod]["cost"] += m["cost"]

        hourly[m["hour"]]["count"] += 1
        hourly[m["hour"]]["tokens"] += m["input_tokens"] + m["output_tokens"]

        agent[m["agent"]]["count"] += 1
        agent[m["agent"]]["tokens"] += m["input_tokens"] + m["output_tokens"]

        sid = m["session_title"]
        session[sid]["title"] = sid
        session[sid]["count"] += 1
        session[sid]["input"] += m["input_tokens"]
        session[sid]["output"] += m["output_tokens"]
        session[sid]["cache_read"] += m["cache_read"]

        dow[m["day_of_week"]]["count"] += 1
        dow[m["day_of_week"]]["tokens"] += m["input_tokens"] + m["output_tokens"]

        model_daily[mod][d]["input"] += m["input_tokens"]
        model_daily[mod][d]["output"] += m["output_tokens"]

    return daily, model, hourly, agent, session, dow, model_daily


def fmt_tokens(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def generate_html(messages, daily, model, hourly, agent, session, dow, model_daily):
    # Totals
    total_input = sum(m["input_tokens"] for m in messages)
    total_output = sum(m["output_tokens"] for m in messages)
    total_cache_read = sum(m["cache_read"] for m in messages)
    total_cache_write = sum(m["cache_write"] for m in messages)
    total_reasoning = sum(m["reasoning_tokens"] for m in messages)
    total_cost = sum(m["cost"] for m in messages)
    total_messages = len(messages)

    # Dates sorted
    dates = sorted(daily.keys())
    if dates:
        first_date = dates[0]
        last_date = dates[-1]
        num_days = (datetime.strptime(last_date, "%Y-%m-%d") - datetime.strptime(first_date, "%Y-%m-%d")).days + 1
    else:
        num_days = 1

    # Daily chart data
    daily_dates = json.dumps(dates)
    daily_input = json.dumps([daily[d]["input"] for d in dates])
    daily_output = json.dumps([daily[d]["output"] for d in dates])
    daily_cache_read = json.dumps([daily[d]["cache_read"] for d in dates])
    daily_count = json.dumps([daily[d]["count"] for d in dates])

    # Model chart data
    model_names = sorted(model.keys(), key=lambda x: model[x]["input"] + model[x]["output"], reverse=True)
    model_labels = json.dumps([n.split("/")[-1] if "/" in n else n for n in model_names])
    model_input_data = json.dumps([model[n]["input"] for n in model_names])
    model_output_data = json.dumps([model[n]["output"] for n in model_names])
    model_cache_data = json.dumps([model[n]["cache_read"] for n in model_names])
    model_counts = json.dumps([model[n]["count"] for n in model_names])

    # Hourly chart
    hourly_labels = json.dumps([f"{h:02d}:00" for h in range(24)])
    hourly_tokens = json.dumps([hourly[h]["tokens"] for h in range(24)])
    hourly_counts = json.dumps([hourly[h]["count"] for h in range(24)])

    # Agent chart
    agent_names = sorted(agent.keys(), key=lambda x: agent[x]["tokens"], reverse=True)
    agent_labels = json.dumps(agent_names[:15])
    agent_tokens_data = json.dumps([agent[a]["tokens"] for a in agent_names[:15]])
    agent_counts_data = json.dumps([agent[a]["count"] for a in agent_names[:15]])

    # Day of week
    dow_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    dow_labels = json.dumps(dow_order)
    dow_tokens_data = json.dumps([dow[d]["tokens"] for d in dow_order])
    dow_counts_data = json.dumps([dow[d]["count"] for d in dow_order])

    # Top sessions
    top_sessions = sorted(session.values(), key=lambda x: x["input"] + x["output"], reverse=True)[:20]

    # Model table rows
    model_rows = ""
    for n in model_names:
        m = model[n]
        total = m["input"] + m["output"]
        short_name = n.split("/")[-1] if "/" in n else n
        model_rows += f"""
        <tr>
            <td><span class="model-badge">{short_name}</span></td>
            <td class="num">{m['count']}</td>
            <td class="num">{fmt_tokens(m['input'])}</td>
            <td class="num">{fmt_tokens(m['output'])}</td>
            <td class="num">{fmt_tokens(m['cache_read'])}</td>
            <td class="num">{fmt_tokens(m['cache_write'])}</td>
            <td class="num">{fmt_tokens(total)}</td>
            <td class="num">${m['cost']:.4f}</td>
        </tr>"""

    # Session table rows
    session_rows = ""
    for s in top_sessions:
        total = s["input"] + s["output"]
        title = s["title"][:60] + ("..." if len(s["title"]) > 60 else "")
        session_rows += f"""
        <tr>
            <td title="{s['title']}">{title}</td>
            <td class="num">{s['count']}</td>
            <td class="num">{fmt_tokens(s['input'])}</td>
            <td class="num">{fmt_tokens(s['output'])}</td>
            <td class="num">{fmt_tokens(s['cache_read'])}</td>
            <td class="num">{fmt_tokens(total)}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MiMo Code - Token Usage Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root {{
    --bg: #0f1117;
    --surface: #1a1d27;
    --surface2: #232733;
    --border: #2e3348;
    --text: #e4e6f0;
    --text-dim: #8b8fa3;
    --accent: #6c5ce7;
    --accent2: #00cec9;
    --green: #00b894;
    --orange: #fdcb6e;
    --red: #e17055;
    --blue: #74b9ff;
    --pink: #fd79a8;
    --purple: #a29bfe;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: 'Segoe UI', -apple-system, system-ui, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: 24px;
}}
.header {{
    text-align: center;
    margin-bottom: 32px;
}}
.header h1 {{
    font-size: 28px;
    font-weight: 700;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 8px;
}}
.header .subtitle {{
    color: var(--text-dim);
    font-size: 14px;
}}
.header .refresh-time {{
    color: var(--text-dim);
    font-size: 12px;
    margin-top: 4px;
}}
.stats-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
    margin-bottom: 32px;
}}
.stat-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    text-align: center;
    transition: transform 0.2s, border-color 0.2s;
}}
.stat-card:hover {{
    transform: translateY(-2px);
    border-color: var(--accent);
}}
.stat-card .value {{
    font-size: 28px;
    font-weight: 700;
    color: var(--text);
    margin-bottom: 4px;
}}
.stat-card .label {{
    font-size: 12px;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
.stat-card.accent .value {{ color: var(--accent); }}
.stat-card.green .value {{ color: var(--green); }}
.stat-card.blue .value {{ color: var(--blue); }}
.stat-card.orange .value {{ color: var(--orange); }}
.stat-card.pink .value {{ color: var(--pink); }}
.stat-card.purple .value {{ color: var(--purple); }}

.charts-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
    gap: 24px;
    margin-bottom: 32px;
}}
.chart-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 24px;
}}
.chart-card h3 {{
    font-size: 16px;
    font-weight: 600;
    margin-bottom: 16px;
    color: var(--text);
}}
.chart-card canvas {{
    max-height: 300px;
}}
.full-width {{
    grid-column: 1 / -1;
}}

.tables-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
    gap: 24px;
    margin-bottom: 32px;
}}
.table-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 24px;
    overflow-x: auto;
}}
.table-card h3 {{
    font-size: 16px;
    font-weight: 600;
    margin-bottom: 16px;
    color: var(--text);
}}
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}}
th {{
    text-align: left;
    padding: 10px 12px;
    border-bottom: 2px solid var(--border);
    color: var(--text-dim);
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
td {{
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
    color: var(--text);
}}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.model-badge {{
    background: var(--surface2);
    padding: 3px 8px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 500;
}}
.footer {{
    text-align: center;
    padding: 24px;
    color: var(--text-dim);
    font-size: 12px;
}}
@media (max-width: 768px) {{
    .charts-grid, .tables-grid {{ grid-template-columns: 1fr; }}
    .stats-grid {{ grid-template-columns: repeat(2, 1fr); }}
    body {{ padding: 12px; }}
}}
</style>
</head>
<body>
<div class="header">
    <h1>MiMo Code Token Dashboard</h1>
    <div class="subtitle">Monitoring penggunaan token dan biaya</div>
    <div class="refresh-time">Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | Period: {first_date} to {last_date} ({num_days} days)</div>
</div>

<div class="stats-grid">
    <div class="stat-card accent">
        <div class="value">{fmt_tokens(total_input)}</div>
        <div class="label">Input Tokens</div>
    </div>
    <div class="stat-card green">
        <div class="value">{fmt_tokens(total_output)}</div>
        <div class="label">Output Tokens</div>
    </div>
    <div class="stat-card blue">
        <div class="value">{fmt_tokens(total_cache_read)}</div>
        <div class="label">Cache Read</div>
    </div>
    <div class="stat-card orange">
        <div class="value">{fmt_tokens(total_cache_write)}</div>
        <div class="label">Cache Write</div>
    </div>
    <div class="stat-card pink">
        <div class="value">{total_messages:,}</div>
        <div class="label">Messages</div>
    </div>
    <div class="stat-card purple">
        <div class="value">${total_cost:.2f}</div>
        <div class="label">Total Cost</div>
    </div>
</div>

<div class="charts-grid">
    <div class="chart-card full-width">
        <h3>Daily Token Usage</h3>
        <canvas id="dailyChart"></canvas>
    </div>
    <div class="chart-card">
        <h3>Token by Model</h3>
        <canvas id="modelChart"></canvas>
    </div>
    <div class="chart-card">
        <h3>Messages by Model</h3>
        <canvas id="modelCountChart"></canvas>
    </div>
    <div class="chart-card">
        <h3>Activity by Hour (UTC)</h3>
        <canvas id="hourlyChart"></canvas>
    </div>
    <div class="chart-card">
        <h3>Activity by Day of Week</h3>
        <canvas id="dowChart"></canvas>
    </div>
    <div class="chart-card">
        <h3>Token Usage by Agent</h3>
        <canvas id="agentChart"></canvas>
    </div>
    <div class="chart-card">
        <h3>Messages by Agent</h3>
        <canvas id="agentCountChart"></canvas>
    </div>
</div>

<div class="tables-grid">
    <div class="table-card full-width">
        <h3>Model Breakdown</h3>
        <table>
            <thead>
                <tr><th>Model</th><th>Messages</th><th>Input</th><th>Output</th><th>Cache Read</th><th>Cache Write</th><th>Total</th><th>Cost</th></tr>
            </thead>
            <tbody>{model_rows}</tbody>
        </table>
    </div>
    <div class="table-card full-width">
        <h3>Top Sessions</h3>
        <table>
            <thead>
                <tr><th>Session</th><th>Messages</th><th>Input</th><th>Output</th><th>Cache Read</th><th>Total</th></tr>
            </thead>
            <tbody>{session_rows}</tbody>
        </table>
    </div>
</div>

<div class="footer">
    MiMo Code Token Dashboard | Data from {DB_PATH}
</div>

<script>
const chartDefaults = {{
    color: '#8b8fa3',
    borderColor: '#2e3348',
}};
Chart.defaults.color = chartDefaults.color;
Chart.defaults.borderColor = chartDefaults.borderColor;

// Daily Chart
new Chart(document.getElementById('dailyChart'), {{
    type: 'line',
    data: {{
        labels: {daily_dates},
        datasets: [
            {{ label: 'Input', data: {daily_input}, borderColor: '#6c5ce7', backgroundColor: 'rgba(108,92,231,0.1)', fill: true, tension: 0.3 }},
            {{ label: 'Output', data: {daily_output}, borderColor: '#00cec9', backgroundColor: 'rgba(0,206,201,0.1)', fill: true, tension: 0.3 }},
            {{ label: 'Cache Read', data: {daily_cache_read}, borderColor: '#74b9ff', backgroundColor: 'rgba(116,185,255,0.1)', fill: true, tension: 0.3 }},
        ]
    }},
    options: {{
        responsive: true,
        interaction: {{ mode: 'index', intersect: false }},
        plugins: {{ legend: {{ position: 'top' }} }},
        scales: {{
            y: {{ ticks: {{ callback: v => v >= 1e6 ? (v/1e6).toFixed(1)+'M' : v >= 1e3 ? (v/1e3).toFixed(1)+'K' : v }} }}
        }}
    }}
}});

// Model Token Chart
new Chart(document.getElementById('modelChart'), {{
    type: 'bar',
    data: {{
        labels: {model_labels},
        datasets: [
            {{ label: 'Input', data: {model_input_data}, backgroundColor: '#6c5ce7' }},
            {{ label: 'Output', data: {model_output_data}, backgroundColor: '#00cec9' }},
            {{ label: 'Cache Read', data: {model_cache_data}, backgroundColor: '#74b9ff' }},
        ]
    }},
    options: {{
        responsive: true,
        indexAxis: 'y',
        plugins: {{ legend: {{ position: 'top' }} }},
        scales: {{
            x: {{ stacked: true, ticks: {{ callback: v => v >= 1e6 ? (v/1e6).toFixed(1)+'M' : v >= 1e3 ? (v/1e3).toFixed(1)+'K' : v }} }},
            y: {{ stacked: true }}
        }}
    }}
}});

// Model Count Chart
new Chart(document.getElementById('modelCountChart'), {{
    type: 'doughnut',
    data: {{
        labels: {model_labels},
        datasets: [{{ data: {model_counts}, backgroundColor: ['#6c5ce7','#00cec9','#00b894','#fdcb6e','#e17055','#74b9ff','#fd79a8','#a29bfe','#55efc4','#fab1a0','#81ecec','#dfe6e9','#636e72','#b2bec3','#ffeaa7'] }}]
    }},
    options: {{
        responsive: true,
        plugins: {{
            legend: {{ position: 'right', labels: {{ boxWidth: 12, padding: 8 }} }}
        }}
    }}
}});

// Hourly Chart
new Chart(document.getElementById('hourlyChart'), {{
    type: 'bar',
    data: {{
        labels: {hourly_labels},
        datasets: [
            {{ label: 'Messages', data: {hourly_counts}, backgroundColor: 'rgba(108,92,231,0.6)', yAxisID: 'y' }},
            {{ label: 'Tokens', data: {hourly_tokens}, type: 'line', borderColor: '#00cec9', yAxisID: 'y1', tension: 0.3, pointRadius: 3 }}
        ]
    }},
    options: {{
        responsive: true,
        plugins: {{ legend: {{ position: 'top' }} }},
        scales: {{
            y: {{ position: 'left', title: {{ display: true, text: 'Messages' }} }},
            y1: {{ position: 'right', grid: {{ drawOnChartArea: false }}, title: {{ display: true, text: 'Tokens' }}, ticks: {{ callback: v => v >= 1e6 ? (v/1e6).toFixed(1)+'M' : v >= 1e3 ? (v/1e3).toFixed(1)+'K' : v }} }}
        }}
    }}
}});

// Day of Week Chart
new Chart(document.getElementById('dowChart'), {{
    type: 'bar',
    data: {{
        labels: {dow_labels},
        datasets: [
            {{ label: 'Messages', data: {dow_counts_data}, backgroundColor: 'rgba(0,184,148,0.6)', yAxisID: 'y' }},
            {{ label: 'Tokens', data: {dow_tokens_data}, type: 'line', borderColor: '#fd79a8', yAxisID: 'y1', tension: 0.3, pointRadius: 4 }}
        ]
    }},
    options: {{
        responsive: true,
        plugins: {{ legend: {{ position: 'top' }} }},
        scales: {{
            y: {{ position: 'left', title: {{ display: true, text: 'Messages' }} }},
            y1: {{ position: 'right', grid: {{ drawOnChartArea: false }}, title: {{ display: true, text: 'Tokens' }}, ticks: {{ callback: v => v >= 1e6 ? (v/1e6).toFixed(1)+'M' : v >= 1e3 ? (v/1e3).toFixed(1)+'K' : v }} }}
        }}
    }}
}});

// Agent Token Chart
new Chart(document.getElementById('agentChart'), {{
    type: 'bar',
    data: {{
        labels: {agent_labels},
        datasets: [{{ label: 'Tokens', data: {agent_tokens_data}, backgroundColor: ['#6c5ce7','#00cec9','#00b894','#fdcb6e','#e17055','#74b9ff','#fd79a8','#a29bfe','#55efc4','#fab1a0','#81ecec','#dfe6e9','#636e72','#b2bec3','#ffeaa7'] }}]
    }},
    options: {{
        responsive: true,
        indexAxis: 'y',
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
            x: {{ ticks: {{ callback: v => v >= 1e6 ? (v/1e6).toFixed(1)+'M' : v >= 1e3 ? (v/1e3).toFixed(1)+'K' : v }} }}
        }}
    }}
}});

// Agent Count Chart
new Chart(document.getElementById('agentCountChart'), {{
    type: 'doughnut',
    data: {{
        labels: {agent_labels},
        datasets: [{{ data: {agent_counts_data}, backgroundColor: ['#6c5ce7','#00cec9','#00b894','#fdcb6e','#e17055','#74b9ff','#fd79a8','#a29bfe','#55efc4','#fab1a0','#81ecec','#dfe6e9','#636e72','#b2bec3','#ffeaa7'] }}]
    }},
    options: {{
        responsive: true,
        plugins: {{
            legend: {{ position: 'right', labels: {{ boxWidth: 12, padding: 8 }} }}
        }}
    }}
}});
</script>
</body>
</html>"""
    return html


def main():
    print("Fetching data from database...")
    messages = fetch_data(DB_PATH)
    print(f"Found {len(messages)} messages with token data")

    if not messages:
        print("No token data found!")
        return

    print("Aggregating data...")
    daily, model, hourly, agent, session, dow, model_daily = aggregate(messages)

    print("Generating HTML dashboard...")
    html = generate_html(messages, daily, model, hourly, agent, session, dow, model_daily)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Dashboard saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
