#!/usr/bin/env python3
"""Aruba CX Switch Monitor - Scheduled monitoring script

Runs on the switch via systemd timer. Checks:
- Port status (down ports that should be up)
- LLDP neighbors (new/missing neighbors)
- Log anomalies (error/critical events)
- VLAN status
- Interface errors/counters

Uses the Ollama LLM to analyze results for anomalies.
Logs findings to switch syslog (tag: AI-MONITOR).
Can also send alerts to a webhook or write to a report file.

Usage:
  python3 /tmp/monitor.py              # Run all checks once
  python3 /tmp/monitor.py --check ports  # Only check ports
  python3 /tmp/monitor.py --check lldp   # Only check LLDP
  python3 /tmp/monitor.py --check logs    # Only check logs
  python3 /tmp/monitor.py --report        # Generate report file
"""

import requests
import json
import subprocess
import sys
import os
import time
from datetime import datetime

OLLAMA_URL = "http://YOUR_OLLAMA_SERVER:11434"
API_KEY = "your-api-key"
MODEL = "glm-5.2:cloud"

REPORT_FILE = "/tmp/monitor_report.txt"
STATE_FILE = "/tmp/monitor_state.json"

# ---------- Utility Functions ----------

def log(msg, level="info"):
    """Log to switch syslog and stdout"""
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        subprocess.run(
            ["logger", "-t", "AI-MONITOR", "-p", f"user.{level}", msg],
            capture_output=True, text=True, timeout=3
        )
    except Exception:
        pass


def run_cli(command):
    """Run a vtysh CLI command"""
    try:
        result = subprocess.run(
            ["vtysh", "-c", command],
            capture_output=True, text=True, timeout=15
        )
        output = result.stdout + result.stderr
        return output.strip() if output.strip() else "(no output)"
    except Exception as e:
        return f"Error: {e}"


def llm_analyze(prompt, max_tokens=500):
    """Ask the LLM to analyze something and return a short response"""
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": "You are a network monitoring AI running on an Aruba CX switch. Analyze the provided data and report any anomalies, issues, or concerning patterns. Be concise and specific. If everything looks normal, say so briefly."},
                    {"role": "user", "content": prompt}
                ],
                "stream": False
            },
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"(LLM analysis unavailable: {e})"


def load_state():
    """Load previous state for comparison"""
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    """Save current state for next comparison"""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


# ---------- Monitoring Checks ----------

def check_ports():
    """Check all port statuses. Report ports that are down."""
    log("Checking port statuses...")
    output = run_cli("show interface")
    
    # Parse port statuses
    ports_up = []
    ports_down = []
    ports_error = []
    
    for line in output.split("\n"):
        if line.startswith("Interface ") and " is " in line:
            parts = line.split()
            port = parts[1]
            status = parts[3]
            if status == "up":
                ports_up.append(port)
            elif "down" in status:
                ports_down.append(port)
        
        # Check for errors
        if "Errors" in line and len(line.split()) > 0 and line.split()[-1] != "0":
            ports_error.append(line.strip())
    
    # Get detailed error stats for ports that have errors
    error_details = ""
    if ports_error:
        error_details = run_cli("show interface") 
    
    analysis = llm_analyze(
        f"Analyze these Aruba CX switch port statuses:\n\n"
        f"Ports UP ({len(ports_up)}): {', '.join(ports_up[:20])}\n"
        f"Ports DOWN ({len(ports_down)}): {', '.join(ports_down[:20])}\n\n"
        f"Are there any ports that should likely be up but are down? "
        f"Any patterns suggesting issues? Be concise."
    )
    
    result = {
        "check": "ports",
        "ports_up": len(ports_up),
        "ports_down": len(ports_down),
        "ports_up_list": ports_up,
        "ports_down_list": ports_down,
        "analysis": analysis
    }
    
    log(f"Port check: {len(ports_up)} up, {len(ports_down)} down")
    log(f"Analysis: {analysis[:200]}")
    return result


def check_lldp():
    """Check LLDP neighbors. Compare with previous state."""
    log("Checking LLDP neighbors...")
    output = run_cli("show lldp neighbor")
    
    # Parse neighbors - look for lines with port info (skip headers)
    neighbors = []
    in_table = False
    for line in output.split("\n"):
        # Skip header lines and separator lines
        if "LOCAL-PORT" in line or "Total Neighbor" in line or line.startswith("===") or line.startswith("---"):
            in_table = True
            continue
        if in_table and line.strip() and not line.startswith("LLDP"):
            neighbors.append(line.strip())
    
    prev_state = load_state()
    prev_neighbors = set(prev_state.get("lldp_neighbors_list", []))
    current_set = set(neighbors)
    
    new_neighbors = current_set - prev_neighbors
    missing_neighbors = prev_neighbors - current_set
    
    analysis = llm_analyze(
        f"Analyze LLDP neighbors on this Aruba CX switch:\n\n"
        f"Current neighbors ({len(neighbors)}):\n{chr(10).join(neighbors[:20])}\n\n"
        f"New neighbors since last check: {len(new_neighbors)}\n"
        f"Missing neighbors: {len(missing_neighbors)}\n\n"
        f"Any concerns? Missing neighbors may indicate disconnected devices."
    )
    
    result = {
        "check": "lldp",
        "neighbor_count": len(neighbors),
        "neighbors": neighbors,
        "new_neighbors": list(new_neighbors),
        "missing_neighbors": list(missing_neighbors),
        "analysis": analysis
    }
    
    log(f"LLDP check: {len(neighbors)} neighbors, {len(new_neighbors)} new, {len(missing_neighbors)} missing")
    if new_neighbors:
        log(f"  NEW: {list(new_neighbors)[:3]}")
    if missing_neighbors:
        log(f"  MISSING: {list(missing_neighbors)[:3]}", "warning")
    log(f"Analysis: {analysis[:200]}")
    return result


def check_logs():
    """Check switch logs for errors and anomalies."""
    log("Checking switch logs for anomalies...")
    output = run_cli("show logging")
    
    # Filter for error/critical/warning entries
    error_lines = []
    warning_lines = []
    for line in output.split("\n"):
        if "LOG_CRIT" in line or "LOG_ERR" in line:
            error_lines.append(line.strip())
        elif "LOG_WARNING" in line or "LOG_NOTICE" in line:
            warning_lines.append(line.strip())
    
    # Get recent logs (last 50 lines)
    recent_logs = output.split("\n")[-50:]
    
    analysis = llm_analyze(
        f"Analyze these Aruba CX switch logs for anomalies.\n\n"
        f"Error/Critical entries ({len(error_lines)}):\n{chr(10).join(error_lines[-10:])}\n\n"
        f"Warning entries ({len(warning_lines)}):\n{chr(10).join(warning_lines[-10:])}\n\n"
        f"Recent log entries (last 20):\n{chr(10).join(recent_logs[-20:])}\n\n"
        f"Are there any anomalies, security concerns, or patterns indicating issues? "
        f"Ignore routine DHCP renewals and NAE agent messages. Focus on real issues."
    )
    
    result = {
        "check": "logs",
        "error_count": len(error_lines),
        "warning_count": len(warning_lines),
        "error_lines": error_lines[-10:],
        "warning_lines": warning_lines[-10:],
        "analysis": analysis
    }
    
    log(f"Log check: {len(error_lines)} errors, {len(warning_lines)} warnings")
    if error_lines:
        log(f"  Recent errors: {error_lines[-3:]}", "warning")
    log(f"Analysis: {analysis[:200]}")
    return result


def check_vlans():
    """Check VLAN status. Look for unexpected VLANs or down VLANs."""
    log("Checking VLAN status...")
    output = run_cli("show vlan")
    
    analysis = llm_analyze(
        f"Analyze the VLAN table on this Aruba CX switch:\n\n{output}\n\n"
        f"Are there any unexpected VLANs? Any VLANs that are down when they shouldn't be? "
        f"Any configuration issues?"
    )
    
    result = {
        "check": "vlans",
        "vlan_output": output,
        "analysis": analysis
    }
    
    log(f"VLAN check complete")
    log(f"Analysis: {analysis[:200]}")
    return result


def check_interfaces_errors():
    """Check interface error counters for ports with errors."""
    log("Checking interface error counters...")
    output = run_cli("show interface")
    
    # Look for ports with non-zero errors
    ports_with_errors = []
    current_port = None
    for line in output.split("\n"):
        if line.startswith("Interface ") and " is " in line:
            current_port = line.split()[1]
        if "Errors" in line and current_port:
            parts = line.split()
            if len(parts) >= 2 and parts[-1] != "0":
                ports_with_errors.append(f"{current_port}: {line.strip()}")
    
    analysis = llm_analyze(
        f"Analyze interface error counters on this Aruba CX switch:\n\n"
        f"Ports with errors:\n{chr(10).join(ports_with_errors) if ports_with_errors else 'None'}\n\n"
        f"Any ports showing concerning error rates? What actions are recommended?"
    )
    
    result = {
        "check": "interface_errors",
        "ports_with_errors": ports_with_errors,
        "error_count": len(ports_with_errors),
        "analysis": analysis
    }
    
    log(f"Interface error check: {len(ports_with_errors)} ports with errors")
    log(f"Analysis: {analysis[:200]}")
    return result


# ---------- Main ----------

def run_all_checks():
    """Run all monitoring checks and generate a report"""
    log("=== Starting monitoring cycle ===")
    
    results = {}
    results["timestamp"] = datetime.utcnow().isoformat() + "Z"
    results["ports"] = check_ports()
    results["lldp"] = check_lldp()
    results["logs"] = check_logs()
    results["vlans"] = check_vlans()
    results["interface_errors"] = check_interfaces_errors()
    
    # Save state for next cycle
    state = {
        "lldp_neighbors_list": results["lldp"].get("neighbors", []),
        "ports_up": results["ports"].get("ports_up", 0),
        "ports_down": results["ports"].get("ports_down", 0),
        "last_run": results["timestamp"]
    }
    save_state(state)
    
    # Generate report
    report = generate_report(results)
    
    # Write report to file
    with open(REPORT_FILE, "w") as f:
        f.write(report)
    
    log(f"Report saved to {REPORT_FILE}")
    log("=== Monitoring cycle complete ===")
    
    return report


def generate_report(results):
    """Generate a readable report from check results"""
    report = []
    report.append("=" * 60)
    report.append(f"Aruba CX Switch Monitor Report")
    report.append(f"Generated: {results['timestamp']}")
    report.append("=" * 60)
    
    # Port status
    p = results.get("ports", {})
    report.append(f"\n--- PORT STATUS ---")
    report.append(f"  UP: {p.get('ports_up', 0)}  DOWN: {p.get('ports_down', 0)}")
    if p.get("ports_down_list"):
        report.append(f"  Down ports: {', '.join(p['ports_down_list'][:20])}")
    report.append(f"  AI Analysis: {p.get('analysis', 'N/A')}")
    
    # LLDP
    l = results.get("lldp", {})
    report.append(f"\n--- LLDP NEIGHBORS ---")
    report.append(f"  Total: {l.get('neighbor_count', 0)}")
    if l.get("new_neighbors"):
        report.append(f"  NEW: {l['new_neighbors'][:5]}")
    if l.get("missing_neighbors"):
        report.append(f"  MISSING: {l['missing_neighbors'][:5]}")
    report.append(f"  AI Analysis: {l.get('analysis', 'N/A')}")
    
    # Logs
    lg = results.get("logs", {})
    report.append(f"\n--- LOG ANALYSIS ---")
    report.append(f"  Errors: {lg.get('error_count', 0)}  Warnings: {lg.get('warning_count', 0)}")
    if lg.get("error_lines"):
        for e in lg["error_lines"][-5:]:
            report.append(f"  ERROR: {e}")
    report.append(f"  AI Analysis: {lg.get('analysis', 'N/A')}")
    
    # VLANs
    v = results.get("vlans", {})
    report.append(f"\n--- VLAN STATUS ---")
    report.append(f"  AI Analysis: {v.get('analysis', 'N/A')}")
    
    # Interface errors
    ie = results.get("interface_errors", {})
    report.append(f"\n--- INTERFACE ERRORS ---")
    report.append(f"  Ports with errors: {ie.get('error_count', 0)}")
    if ie.get("ports_with_errors"):
        for e in ie["ports_with_errors"][:10]:
            report.append(f"  {e}")
    report.append(f"  AI Analysis: {ie.get('analysis', 'N/A')}")
    
    report.append("\n" + "=" * 60)
    return "\n".join(report)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--check":
            check_name = sys.argv[2] if len(sys.argv) > 2 else "all"
            if check_name == "ports":
                result = check_ports()
            elif check_name == "lldp":
                result = check_lldp()
            elif check_name == "logs":
                result = check_logs()
            elif check_name == "vlans":
                result = check_vlans()
            elif check_name == "errors":
                result = check_interfaces_errors()
            else:
                result = run_all_checks()
            print(json.dumps(result, indent=2, default=str))
        elif sys.argv[1] == "--report":
            print(run_all_checks())
        else:
            print(f"Usage: {sys.argv[0]} [--check ports|lldp|logs|vlans|errors] [--report]")
    else:
        print(run_all_checks())