#!/usr/bin/env python3
"""TokenPulse Lightweight Server - Serves dashboard with live data API."""

import http.server
import json
import os
import sys
import urllib.request
import sqlite3
from datetime import datetime
from collections import defaultdict

PORT = 8080
DB_PATH = os.path.expanduser("~/.local/share/mimocode/mimocode.db")
ROUTER_URL = "http://localhost:20128"
ROUTER_PASSWORD = "123456"
DASHBOARD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mimo-token-dashboard.html")


def fetch_mimo_data():
    """Fetch token usage data from MiMo Code SQLite database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT m.data, m.time_created, m.agent_id, s.title as session_title
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
                        "cache_read": tokens.get("cache", {}).get("read", 0),
                        "cache_write": tokens.get("cache", {}).get("write", 0),
                        "cost": data.get("cost", 0),
                    })
            except (json.JSONDecodeError, TypeError):
                continue
        conn.close()
        return messages
    except Exception as e:
        print(f"Error fetching MiMo data: {e}")
        return []


def fetch_9router_data():
    """Fetch token usage data from 9Router API."""
    try:
        login_data = json.dumps({"password": ROUTER_PASSWORD}).encode("utf-8")
        login_req = urllib.request.Request(
            f"{ROUTER_URL}/api/auth/login",
            data=login_data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        login_resp = urllib.request.urlopen(login_req, timeout=5)
        cookies = login_resp.headers.get_all("Set-Cookie") or []
        cookie_str = "; ".join(c.split(";")[0] for c in cookies) if cookies else ""
        stats_req = urllib.request.Request(
            f"{ROUTER_URL}/api/usage/stats?period=60d",
            headers={"Cookie": cookie_str, "Content-Type": "application/json"}
        )
        stats_resp = urllib.request.urlopen(stats_req, timeout=5)
        return json.loads(stats_resp.read().decode("utf-8"))
    except Exception as e:
        print(f"Warning: Could not fetch 9Router data: {e}")
        return None


def aggregate_data(mimo_messages, router_stats):
    """Aggregate data from both sources."""
    daily = defaultdict(lambda: {"input": 0, "output": 0, "cache_read": 0, "count": 0, "cost": 0})
    model = defaultdict(lambda: {"input": 0, "output": 0, "cache_read": 0, "count": 0, "cost": 0})
    hourly = defaultdict(lambda: {"count": 0, "tokens": 0})
    provider = defaultdict(lambda: {"input": 0, "output": 0, "count": 0, "cost": 0})
    session = defaultdict(lambda: {"title": "", "count": 0, "input": 0, "output": 0, "cache_read": 0})

    # Process MiMo messages
    for m in mimo_messages:
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

    # Add 9Router recent requests
    if router_stats and "recentRequests" in router_stats:
        for req in router_stats["recentRequests"]:
            try:
                dt = datetime.fromisoformat(req["timestamp"].replace("Z", "+00:00"))
                all_messages_entry = {
                    "date": dt.strftime("%Y-%m-%d"),
                    "hour": dt.hour,
                    "model": req.get("model", "unknown"),
                    "provider": req.get("provider", "9router"),
                    "session_title": f"9Router - {req.get('model', 'unknown')}",
                    "input_tokens": req.get("promptTokens", 0),
                    "output_tokens": req.get("completionTokens", 0),
                    "cache_read": req.get("cachedTokens", 0),
                    "cost": 0,
                }
                d = all_messages_entry["date"]
                daily[d]["input"] += all_messages_entry["input_tokens"]
                daily[d]["output"] += all_messages_entry["output_tokens"]
                daily[d]["cache_read"] += all_messages_entry["cache_read"]
                daily[d]["count"] += 1

                mod = all_messages_entry["model"]
                model[mod]["input"] += all_messages_entry["input_tokens"]
                model[mod]["output"] += all_messages_entry["output_tokens"]
                model[mod]["cache_read"] += all_messages_entry["cache_read"]
                model[mod]["count"] += 1

                hourly[all_messages_entry["hour"]]["count"] += 1
                hourly[all_messages_entry["hour"]]["tokens"] += all_messages_entry["input_tokens"] + all_messages_entry["output_tokens"]

                prov = all_messages_entry["provider"]
                provider[prov]["input"] += all_messages_entry["input_tokens"]
                provider[prov]["output"] += all_messages_entry["output_tokens"]
                provider[prov]["count"] += 1

                sid = all_messages_entry["session_title"]
                session[sid]["title"] = sid
                session[sid]["count"] += 1
                session[sid]["input"] += all_messages_entry["input_tokens"]
                session[sid]["output"] += all_messages_entry["output_tokens"]
                session[sid]["cache_read"] += all_messages_entry["cache_read"]
            except (ValueError, KeyError):
                continue

    # Calculate totals
    total_input = sum(daily[d]["input"] for d in daily)
    total_output = sum(daily[d]["output"] for d in daily)
    total_cache_read = sum(daily[d]["cache_read"] for d in daily)
    total_cost = sum(daily[d]["cost"] for d in daily)
    total_messages = sum(daily[d]["count"] for d in daily)

    # Add 9Router aggregated totals
    if router_stats:
        total_input += router_stats.get("totalPromptTokens", 0)
        total_output += router_stats.get("totalCompletionTokens", 0)
        total_cache_read += router_stats.get("totalCachedTokens", 0)
        total_cost += router_stats.get("totalCost", 0)
        total_messages += router_stats.get("totalRequests", 0)

    return {
        "totals": {
            "input": total_input,
            "output": total_output,
            "cache_read": total_cache_read,
            "cost": total_cost,
            "messages": total_messages,
            "requests": total_messages,
        },
        "daily": dict(daily),
        "model": {k: dict(v) for k, v in model.items()},
        "hourly": {str(k): dict(v) for k, v in hourly.items()},
        "provider": {k: dict(v) for k, v in provider.items()},
        "session": {k: dict(v) for k, v in session.items()},
        "router": router_stats,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/data":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            mimo_messages = fetch_mimo_data()
            router_stats = fetch_9router_data()
            data = aggregate_data(mimo_messages, router_stats)
            self.wfile.write(json.dumps(data).encode("utf-8"))
        elif self.path == "/" or self.path == "/dashboard":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            with open(DASHBOARD_PATH, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}")


def main():
    print(f"TokenPulse Server starting on http://localhost:{PORT}")
    print(f"Dashboard: http://localhost:{PORT}/")
    print(f"API: http://localhost:{PORT}/api/data")
    print("Press Ctrl+C to stop\n")
    server = http.server.HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
