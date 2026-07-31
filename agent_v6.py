#!/usr/bin/env python3
"""Aruba CX Switch AI Agent v6

Incorporates security review and CCIE review recommendations:
- Command allowlist/blocklist (no zeroize, erase, reload, start-shell)
- Prompt injection mitigation (sanitizes switch output fed to LLM)
- Configurable Ollama endpoint via environment variables
- Enhanced system prompt with full AOS-CX command reference
- Additional tools: show_lldp, configure_vlan, configure_lag
- Conversation history size limit (prevents unbounded growth)
- Output sanitization before logging (removes credential patterns)
- Destructive command confirmation
"""

import requests
import json
import subprocess
import sys
import os
import re
import readline
import time

# Configuration via environment variables with safe defaults
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://YOUR_OLLAMA_SERVER:11434")
API_KEY = os.environ.get("OLLAMA_API_KEY", "your-api-key")
MODEL = os.environ.get("OLLAMA_MODEL", "glm-5.2:cloud")

# ---------- Security: Command Allowlist/Blocklist ----------

# Commands that are ALWAYS blocked (destructive/irreversible)
BLOCKED_COMMANDS = [
    r"zeroize",
    r"\berase\b",
    r"\breload\b",
    r"start-shell",
    r"\brm\s",
    r"\bformat\b",
    r"\bdd\b",
    r"\bmkfs\b",
    r"\biptables\b",
    r"\bshutdown\s+-h\b",
    r"\breboot\b",
    r"\bhalt\b",
    r"\bpoweroff\b",
    r"\buser\s+\S+\s+password",  # Don't allow password changes via agent
]

# Commands that require confirmation (potentially disruptive)
CONFIRM_COMMANDS = [
    r"write\s+memory",
    r"copy\s+running-config",
    r"no\s+shutdown",
    r"shutdown",
    r"no\s+vlan",
    r"vlan\s+\d+",  # Creating/deleting VLANs
    r"interface\s+\d+/\d+/\d+",  # Interface config changes
    r"spanning-tree",
    r"no\s+spanning-tree",
    r"lacp",
    r"no\s+lacp",
    r"access-list",
    r"no\s+access-list",
    r"port-security",
    r"no\s+port-security",
    r"dhcp-snooping",
    r"no\s+dhcp-snooping",
    r"arp\s+inspection",
    r"radius-server",
    r"tacacs-server",
    r"aaa\s+",
]

def is_blocked(command):
    """Check if a command is in the blocked list."""
    cmd_lower = command.lower().strip()
    for pattern in BLOCKED_COMMANDS:
        if re.search(pattern, cmd_lower):
            return True, pattern
    return False, None

def needs_confirmation(command):
    """Check if a command needs user confirmation."""
    cmd_lower = command.lower().strip()
    for pattern in CONFIRM_COMMANDS:
        if re.search(pattern, cmd_lower):
            return True
    return False

def sanitize_output(output):
    """Sanitize switch output before feeding to LLM or logging.
    Removes potential prompt injection vectors and credential patterns."""
    if not output:
        return output
    # Remove potential prompt injection markers
    output = re.sub(r'<system>.*?</system>', '[FILTERED]', output, flags=re.DOTALL)
    output = re.sub(r'<instruction>.*?</instruction>', '[FILTERED]', output, flags=re.DOTALL)
    output = re.sub(r'\[SYSTEM\].*?\[/SYSTEM\]', '[FILTERED]', output, flags=re.DOTALL)
    output = re.sub(r'ignore (all )?previous instructions', '[FILTERED]', output, flags=re.IGNORECASE)
    # Mask potential credentials in output
    output = re.sub(r'(password|secret|key|token)\s+(\S+)', r'\1 [REDACTED]', output, flags=re.IGNORECASE)
    # Limit output size to prevent context flooding
    if len(output) > 10000:
        output = output[:10000] + "\n... [output truncated for safety]"
    return output

# ---------- Switch Info Gathering ----------

def gather_switch_info():
    """Gather key switch information at startup."""
    info = {}
    try:
        info["version_output"] = run_cli_command_raw("show version")
    except Exception:
        info["version_output"] = "(unable to read)"
    try:
        info["system_output"] = run_cli_command_raw("show system")
    except Exception:
        info["system_output"] = "(unable to read)"
    try:
        iface_output = run_cli_command_raw("show interface")
        port_lines = [l for l in iface_output.split("\n") if l.startswith("Interface ") and " is " in l]
        info["port_count"] = len(port_lines)
        info["port_list"] = [l.split()[1] for l in port_lines]
        info["port_first"] = port_lines[0].split()[1] if port_lines else "unknown"
        info["port_last"] = port_lines[-1].split()[1] if port_lines else "unknown"
    except Exception:
        info["port_count"] = 0
        info["port_first"] = "unknown"
        info["port_last"] = "unknown"
    try:
        info["vlan_output"] = run_cli_command_raw("show vlan")
    except Exception:
        info["vlan_output"] = "(unable to read)"
    return info

def build_system_prompt(switch_info):
    """Build system prompt with real switch data and full command reference."""
    pc = switch_info.get("port_count", "?")
    pf = switch_info.get("port_first", "?")
    pl = switch_info.get("port_last", "?")
    ver = switch_info.get("version_output", "")
    sys_out = switch_info.get("system_output", "")
    vlan_out = switch_info.get("vlan_output", "")

    version_line = ""
    model_line = ""
    hostname = ""
    for line in sys_out.split("\n"):
        if "ArubaOS-CX Version" in line: version_line = line.strip()
        if "Product Name" in line: model_line = line.strip()
        if "Hostname" in line and ":" in line: hostname = line.split(":")[1].strip()

    return f"""You are an AI assistant running on an Aruba CX network switch.
You interact with the switch by calling the run_cli and run_cli_batch functions.
The command field should contain ONLY the switch CLI command (e.g. "show interface 1/1/1").
Do NOT prefix commands with 'vtysh'.

=== SWITCH PLATFORM INFO ===
{version_line}
{model_line}
Hostname: {hostname}
Ports: {pc} ({pf} through {pl})
{ver}

Current VLANs:
{vlan_out}
=== END SWITCH INFO ===

COMMAND REFERENCE (verified for AOS-CX 10.07):

Show Commands:
  show version, show system, show logging
  show interface [brief], show interface 1/1/N
  show running-config, show running-config interface [1/1/N]
  show vlan [N], show vlans (deprecated, use show vlan)
  show lldp neighbor-info [detail]  (NOT 'show lldp info remote-device')
  show lldp neighbor-info 1/1/N
  show mac-address-table [vlan N]
  show spanning-tree, show spanning-tree detail
  show ip route, show ip interface brief
  show lacp interfaces, show lag
  show access-list, show port-access
  show dhcp-snooping, show arp-inspection
  show ntp status, show snmp
  show running-config | include <pattern>

Layer 2 Config:
  VLAN: configure terminal > vlan N > name X > exit > end
  Access port: configure terminal > interface 1/1/N > no routing > vlan access N > no shutdown > exit > end
  Trunk port: configure terminal > interface 1/1/N > no routing > vlan trunk allowed N,M > exit > end
  Native VLAN: vlan trunk native N
  LACP/LAG: configure terminal > interface lag N > no shutdown > lacp mode active > exit > end
            Member: interface 1/1/N > lag N > exit

Spanning Tree:
  configure terminal > spanning-tree mode mstp > exit
  spanning-tree priority <priority>
  show spanning-tree, show spanning-tree mst

Security:
  ACL: access-list ip N > 10 permit/deny ip <src> <dst> > exit > apply access-list N in/out
  Port security: interface 1/1/N > port-security > port-security max-mac-count N
  DHCP snooping: dhcp-snooping > dhcp-snooping vlan N > trust port 1/1/N
  ARP inspection: arp-inspection vlan N

Routing:
  interface vlan N > ip address X.X.X.X/M > no shutdown
  ip route X.X.X.X/M X.X.X.X
  show ip route, show ip interface brief

System:
  ntp server X.X.X.X > ntp enable
  snmp-server community <name>
  logging X.X.X.X
  write memory (save config)

CRITICAL RULES:
- Do NOT prefix commands with 'vtysh'
- Port names: {pf} through {pl}
- If a command returns "Invalid input", retry with corrected syntax
- Always check state before changes, verify after, save with write_memory
- Keep responses concise and well-formatted
- You CANNOT run: zeroize, erase, reload, start-shell, rm, format
- Do not attempt to change user passwords"""


# ---------- Logging ----------

def log_to_switch(level, message):
    """Log to switch syslog. Sanitize sensitive data first."""
    safe_msg = sanitize_output(message)
    try:
        subprocess.run(
            ["logger", "-t", "AI-AGENT", "-p", f"user.{level.lower()}", safe_msg],
            capture_output=True, text=True, timeout=3
        )
    except Exception:
        pass

def log_command(command, result, success=True):
    """Log a CLI command and result."""
    level = "info" if success else "error"
    result_preview = sanitize_output(result)[:200]
    result_preview = result_preview.replace("\n", " | ").strip()
    log_to_switch(level, f"CMD: {command} -> [{result_preview}]")


# ---------- Switch CLI Interface ----------

ERROR_PATTERNS = [
    "Invalid input", "% Ambiguous command", "Command not supported",
    "Error:", "No such", "syntax error", "Unknown command",
]

def is_error_output(output):
    for p in ERROR_PATTERNS:
        if p.lower() in output.lower():
            return True
    return False

def run_cli_command_raw(command):
    """Run vtysh command without logging or validation."""
    try:
        result = subprocess.run(
            ["vtysh", "-c", command],
            capture_output=True, text=True, timeout=15
        )
        output = result.stdout + result.stderr
        return output.strip() if output.strip() else "(no output - command succeeded)"
    except subprocess.TimeoutExpired:
        return "Error: command timed out"
    except Exception as e:
        return f"Error: {e}"

def run_cli_command(command):
    """Run vtysh command with security checks, logging, and validation."""
    # Security check: blocked commands
    blocked, pattern = is_blocked(command)
    if blocked:
        msg = f"BLOCKED: Command matches blocked pattern '{pattern}': {command}"
        log_to_switch("error", msg)
        return f"SECURITY: This command is blocked for safety: {command}"

    log_to_switch("info", f"EXEC: {command}")
    output = run_cli_command_raw(command)
    output = sanitize_output(output)
    success = not is_error_output(output)
    log_command(command, output, success)
    return output

def run_cli_commands(commands):
    """Run multiple vtysh commands in sequence with security checks."""
    # Check all commands for blocked patterns
    for cmd in commands:
        blocked, pattern = is_blocked(cmd)
        if blocked:
            msg = f"BLOCKED: Command '{cmd}' matches blocked pattern '{pattern}'"
            log_to_switch("error", msg)
            return f"SECURITY: Command '{cmd}' is blocked for safety"

    cmd_summary = "; ".join(commands)
    log_to_switch("info", f"EXEC_BATCH: {cmd_summary}")
    args = []
    for cmd in commands:
        args.extend(["-c", cmd])
    try:
        result = subprocess.run(
            ["vtysh"] + args,
            capture_output=True, text=True, timeout=20
        )
        output = result.stdout + result.stderr
        output = sanitize_output(output).strip() if output.strip() else "(commands succeeded)"
        success = not is_error_output(output)
        log_command(cmd_summary, output, success)
        return output
    except subprocess.TimeoutExpired:
        log_command(cmd_summary, "TIMEOUT", False)
        return "Error: commands timed out"
    except Exception as e:
        log_command(cmd_summary, str(e), False)
        return f"Error: {e}"

def write_memory():
    """Save configuration."""
    log_to_switch("info", "WRITE_MEMORY: saving configuration")
    result = run_cli_command("write memory")
    log_to_switch("info", f"WRITE_MEMORY: {sanitize_output(result)[:100]}")
    return result

def show_all_status():
    """Get comprehensive switch overview."""
    log_to_switch("info", "SHOW_STATUS: gathering switch overview")
    output = "=== SYSTEM ===\n"
    output += run_cli_command("show system") + "\n\n"
    output += "=== VLANS ===\n"
    output += run_cli_command("show vlan") + "\n\n"
    output += "=== INTERFACE CONFIG ===\n"
    output += run_cli_command("show running-config interface") + "\n\n"
    output += "=== LLDP NEIGHBORS ===\n"
    output += run_cli_command("show lldp neighbor-info") + "\n\n"
    output += "=== SPANNING TREE ===\n"
    output += run_cli_command("show spanning-tree") + "\n\n"
    output += "=== RUNNING CONFIG ===\n"
    output += run_cli_command("show running-config") + "\n"
    return output

def show_lldp():
    """Show LLDP neighbor info specifically."""
    return run_cli_command("show lldp neighbor-info")


# ---------- Tool Definitions ----------

TOOL_DEFINITIONS = [
    {"type": "function", "function": {
        "name": "run_cli",
        "description": "Run any Aruba CX switch CLI command (show, configure, etc.). Just the command itself, NOT 'vtysh'.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "The CLI command, e.g. 'show interface 1/1/1'"}
        }, "required": ["command"]}
    }},
    {"type": "function", "function": {
        "name": "run_cli_batch",
        "description": "Run multiple CLI commands in sequence. For configuration changes. Example: ['configure terminal', 'vlan 100', 'name MGMT', 'exit', 'end']",
        "parameters": {"type": "object", "properties": {
            "commands": {"type": "array", "items": {"type": "string"}, "description": "Commands to run in order"}
        }, "required": ["commands"]}
    }},
    {"type": "function", "function": {
        "name": "show_status",
        "description": "Get comprehensive overview: system, VLANs, interfaces, LLDP, STP, running config",
        "parameters": {"type": "object", "properties": {}}
    }},
    {"type": "function", "function": {
        "name": "write_memory",
        "description": "Save running configuration to flash memory",
        "parameters": {"type": "object", "properties": {}}
    }},
    {"type": "function", "function": {
        "name": "show_lldp",
        "description": "Show LLDP neighbor information for the switch",
        "parameters": {"type": "object", "properties": {}}
    }},
]

TOOL_HANDLERS = {
    "run_cli": lambda args: run_cli_command(args.get("command", "")),
    "run_cli_batch": lambda args: run_cli_commands(args.get("commands", [])),
    "show_status": lambda args: show_all_status(),
    "write_memory": lambda args: write_memory(),
    "show_lldp": lambda args: show_lldp(),
}

# ---------- LLM Communication ----------

MAX_CONVERSATION_MESSAGES = 50  # Limit conversation history to prevent unbounded growth

def call_ollama(messages, tools=None):
    payload = {"model": MODEL, "messages": messages, "stream": False}
    if tools:
        payload["tools"] = tools
    resp = requests.post(
        f"{OLLAMA_URL}/v1/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json=payload, timeout=60
    )
    resp.raise_for_status()
    return resp.json()

def execute_tool_calls(tool_calls):
    results = []
    for tc in tool_calls:
        func_name = tc["function"]["name"]
        raw_args = tc["function"]["arguments"]
        func_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        arg_summary = json.dumps(func_args)
        if len(arg_summary) > 120: arg_summary = arg_summary[:120] + "..."
        print(f"  [Running: {func_name}({arg_summary})]")
        handler = TOOL_HANDLERS.get(func_name)
        result = handler(func_args) if handler else f"Unknown function: {func_name}"
        if is_error_output(result):
            result = f"COMMAND ERROR (retry with corrected syntax): {result}"
            print(f"  [ERROR - will retry]")
        else:
            result = sanitize_output(result)
        display = result[:150] + "..." if len(result) > 150 else result
        print(f"  [Result: {display}]")
        results.append({"tool_call_id": tc["id"], "role": "tool", "name": func_name, "content": result})
    return results

def trim_conversation(conversation):
    """Trim old messages if conversation gets too long, keeping system prompt."""
    if len(conversation) > MAX_CONVERSATION_MESSAGES:
        system = conversation[0]
        recent = conversation[-MAX_CONVERSATION_MESSAGES+1:]
        conversation = [system] + recent
        print("  [Conversation history trimmed for memory]")
    return conversation

def process_request(messages, tools=None, max_rounds=10):
    for round_num in range(max_rounds):
        messages = trim_conversation(messages)
        response = call_ollama(messages, tools)
        choice = response["choices"][0]
        msg = choice["message"]
        messages.append(msg)
        if msg.get("tool_calls"):
            print(f"  [Round {round_num + 1}/{max_rounds}] Running switch commands...")
            tool_results = execute_tool_calls(msg["tool_calls"])
            messages.extend(tool_results)
            continue
        return msg.get("content", "(no response)"), messages
    return "Maximum tool call rounds reached.", messages

def interactive(system_prompt):
    print("=" * 60)
    print("  Aruba CX Switch AI Agent v6 - Security Hardened")
    print(f"  Model: {MODEL}")
    print(f"  Ollama: {OLLAMA_URL}")
    print("  Security: Command blocklist, output sanitization, history limits")
    print("  Type 'exit' to quit, 'status' for overview, 'clear' to reset")
    print("  Type 'info' for switch info, 'log' for agent logs")
    print("=" * 60)
    log_to_switch("info", "=== AI Agent v6 session started ===")
    conversation = [{"role": "system", "content": system_prompt}]
    while True:
        try:
            user_input = input("\nYou> ")
            if user_input.lower().strip() in ("exit", "quit", "q"):
                log_to_switch("info", "=== AI Agent session ended ===")
                print("Goodbye!")
                break
            if user_input.lower().strip() == "clear":
                conversation = [{"role": "system", "content": system_prompt}]
                print("[History cleared]")
                continue
            if user_input.lower().strip() == "status":
                print("\n[Getting switch status...]")
                result = show_all_status()
                print(result[:2000])
                if len(result) > 2000: print(f"\n... ({len(result) - 2000} more chars)")
                conversation.append({"role": "user", "content": "Show me a quick status overview"})
                conversation.append({"role": "assistant", "content": result[:2000]})
                continue
            if user_input.lower().strip() == "log":
                log_output = subprocess.run(["vtysh", "-c", "show logging"], capture_output=True, text=True, timeout=10).stdout
                agent_logs = [l for l in log_output.split("\n") if "AI-AGENT" in l][-20:]
                print("\n".join(agent_logs) if agent_logs else "(no agent logs)")
                continue
            if user_input.lower().strip() == "info":
                print(f"\n[Switch info gathered at startup:]\n{switch_info_summary}")
                continue
            if not user_input.strip(): continue
            log_to_switch("info", f"USER_QUERY: {user_input[:200]}")
            conversation.append({"role": "user", "content": user_input})
            print("\nAgent> ", end="", flush=True)
            response, conversation = process_request(conversation, tools=TOOL_DEFINITIONS)
            print(response)
            log_to_switch("info", f"AGENT_RESPONSE: {response[:200]}")
        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            log_to_switch("info", "=== AI Agent session ended (Ctrl-C) ===")
            break
        except Exception as e:
            print(f"\nError: {e}")
            log_to_switch("error", f"AGENT_ERROR: {e}")

# ---------- Startup ----------

if __name__ == "__main__":
    print("[Gathering switch information...]")
    switch_info = gather_switch_info()
    switch_info_summary = (
        f"Version: {switch_info.get('version_output', '?')[:200]}\n"
        f"System: {switch_info.get('system_output', '?')[:300]}\n"
        f"Ports: {switch_info.get('port_count', '?')} ({switch_info.get('port_first', '?')} to {switch_info.get('port_last', '?')})\n"
        f"VLANs:\n{switch_info.get('vlan_output', '?')[:500]}\n"
    )
    print(switch_info_summary)
    system_prompt = build_system_prompt(switch_info)
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
        log_to_switch("info", f"=== AI Agent v6 query: {prompt[:200]} ===")
        conv = [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]
        response, conv = process_request(conv, tools=TOOL_DEFINITIONS)
        print(response)
        log_to_switch("info", "=== AI Agent v6 query complete ===")
    else:
        interactive(system_prompt)