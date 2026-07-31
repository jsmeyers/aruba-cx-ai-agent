#!/usr/bin/env python3
"""Aruba CX Switch AI Agent v5

Improvements over v4:
- Dynamically gathers switch info at startup (version, model, ports, VLANs)
  and injects it into the system prompt so the LLM knows the exact platform
- Error detection: when a CLI command returns "Invalid input" or similar error,
  the agent tells the LLM and it gets a chance to retry with corrected syntax
- Retry loop: up to 10 rounds of tool calls, with error feedback so the LLM
  can self-correct failed commands before giving the final answer
- Up-arrow history via readline
- All commands logged to switch syslog (tag: AI-AGENT)
"""

import requests
import json
import subprocess
import sys
import os
import readline  # Enables up-arrow history, line editing in interactive mode
import time

OLLAMA_URL = "http://YOUR_OLLAMA_SERVER:11434"
API_KEY = "your-api-key"
MODEL = "glm-5.2:cloud"


# ---------- Switch Info Gathering ----------

def gather_switch_info():
    """Gather key switch information at startup.
    Returns a dict with version, model, port count, VLANs, etc."""
    info = {}
    try:
        ver = run_cli_command_raw("show version")
        info["version_output"] = ver
    except Exception:
        info["version_output"] = "(unable to read)"

    try:
        sys_info = run_cli_command_raw("show system")
        info["system_output"] = sys_info
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
        info["port_list"] = []
        info["port_first"] = "unknown"
        info["port_last"] = "unknown"

    try:
        vlan_output = run_cli_command_raw("show vlan")
        info["vlan_output"] = vlan_output
    except Exception:
        info["vlan_output"] = "(unable to read)"

    return info


def build_system_prompt(switch_info):
    """Build a system prompt that includes real switch data."""
    port_count = switch_info.get("port_count", "?")
    port_first = switch_info.get("port_first", "?")
    port_last = switch_info.get("port_last", "?")
    system_output = switch_info.get("system_output", "")
    version_output = switch_info.get("version_output", "")
    vlan_output = switch_info.get("vlan_output", "")

    # Extract key facts from system output
    version_line = ""
    model_line = ""
    hostname = ""
    for line in system_output.split("\n"):
        if "ArubaOS-CX Version" in line:
            version_line = line.strip()
        if "Product Name" in line:
            model_line = line.strip()
        if "Hostname" in line:
            hostname = line.split(":")[1].strip() if ":" in line else ""

    prompt = f"""You are an AI assistant running directly on an Aruba CX network switch.
You interact with the switch by calling the run_cli and run_cli_batch functions.
The switch CLI commands are executed via vtysh - you do NOT type 'vtysh' in the
command field, just the switch CLI command itself (e.g. "show interface 1/1/1").

=== SWITCH PLATFORM INFO (gathered at startup) ===
{version_line}
{model_line}
Hostname: {hostname}
Ports: {port_count} ports ({port_first} through {port_last})
{version_output}

Current VLANs:
{vlan_output}
=== END SWITCH INFO ===

IMPORTANT COMMAND REFERENCE (verified correct for this switch):
- show running-config                 -> full config
- show running-config interface       -> all interface configs (shows descriptions)
- show interface                      -> brief status of ALL ports
- show interface 1/1/1               -> detailed status of port 1/1/1
- show running-config interface 1/1/1 -> config of a specific port
- show vlan                           -> VLAN summary (NOT "show vlans")
- show vlan 100                       -> VLAN 100 details
- show system                         -> system info
- show version                        -> version info
- show lldp info remote-device       -> LLDP neighbors
- show mac-address-table              -> MAC table
- show spanning-tree                   -> STP info
- show ip route                        -> routing table (may be empty)
- show logging                         -> switch event log

CONFIGURATION EXAMPLES (use run_cli_batch with multiple commands):
- Create VLAN: ["configure terminal", "vlan 100", "name MGMT", "exit", "end"]
- Enable port: ["configure terminal", "interface 1/1/1", "no shutdown", "exit", "end"]
- Set description: ["configure terminal", "interface 1/1/1", "description UPLINK-CORE", "exit", "end"]
- Access VLAN: ["configure terminal", "interface 1/1/1", "no routing", "vlan access 100", "exit", "end"]
- Trunk VLAN: ["configure terminal", "interface 1/1/1", "no routing", "vlan trunk allowed 100,200", "exit", "end"]
- Save config: write_memory() or run_cli_batch(["write memory"])

CRITICAL RULES:
- Do NOT prefix commands with 'vtysh' - the agent handles that internally
- The command field should contain ONLY the switch CLI command (e.g. "show vlan")
- Port names on THIS switch: {port_first} through {port_last}
- If a command returns "Invalid input" or an error, that means the syntax is wrong.
  Read the error, figure out the correct command, and retry.
- Always check current state before making changes.
- After making changes, verify with show commands and save with write_memory.
- Keep responses concise and well-formatted."""

    return prompt


# ---------- Logging ----------

def log_to_switch(level, message):
    """Log a message to the switch syslog via Linux logger command."""
    try:
        subprocess.run(
            ["logger", "-t", "AI-AGENT", "-p", f"user.{level.lower()}", message],
            capture_output=True, text=True, timeout=3
        )
    except Exception:
        pass  # Logging is best-effort


def log_command(command, result, success=True):
    """Log a CLI command and its result to the switch syslog."""
    level = "info" if success else "error"
    result_preview = result[:200] + "..." if len(result) > 200 else result
    result_preview = result_preview.replace("\n", " | ").strip()
    msg = f"CMD: {command} -> [{result_preview}]"
    log_to_switch(level, msg)


# ---------- Switch CLI Interface ----------

# Error patterns that indicate a command failed
ERROR_PATTERNS = [
    "Invalid input",
    "% Ambiguous command",
    "Command not supported",
    "Error:",
    "No such",
    "syntax error",
    "Unknown command",
]

def is_error_output(output):
    """Check if the command output indicates an error."""
    for pattern in ERROR_PATTERNS:
        if pattern.lower() in output.lower():
            return True
    return False


def run_cli_command_raw(command):
    """Run a single vtysh CLI command and return output. No logging."""
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
    """Run a single vtysh CLI command and return output. With logging."""
    log_to_switch("info", f"EXEC: {command}")
    output = run_cli_command_raw(command)
    success = not is_error_output(output)
    log_command(command, output, success)
    return output


def run_cli_commands(commands):
    """Run multiple vtysh CLI commands in sequence. With logging."""
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
        output = output.strip() if output.strip() else "(no output - commands succeeded)"
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
    log_to_switch("info", "WRITE_MEMORY: saving configuration to flash")
    result = run_cli_command("write memory")
    log_to_switch("info", f"WRITE_MEMORY: {result}")
    return result


def show_all_status():
    """Get a comprehensive overview of the switch"""
    log_to_switch("info", "SHOW_STATUS: gathering comprehensive switch overview")
    output = "=== SYSTEM ===\n"
    output += run_cli_command("show system") + "\n\n"
    output += "=== VLANS ===\n"
    output += run_cli_command("show vlan") + "\n\n"
    output += "=== INTERFACE CONFIG ===\n"
    output += run_cli_command("show running-config interface") + "\n\n"
    output += "=== LLDP NEIGHBORS ===\n"
    output += run_cli_command("show lldp info remote-device") + "\n\n"
    output += "=== RUNNING CONFIG ===\n"
    output += run_cli_command("show running-config") + "\n"
    return output


# ---------- Tool Definitions for LLM ----------

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "run_cli",
            "description": "Run any Aruba CX switch CLI command. Just the command itself, NOT 'vtysh'. Examples: 'show interface 1/1/1', 'show vlan', 'show running-config interface'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The CLI command to run, e.g. 'show interface 1/1/1' or 'show vlan'"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_cli_batch",
            "description": "Run multiple CLI commands in sequence. Use for configuration changes. Example: ['configure terminal', 'vlan 100', 'name MGMT', 'exit', 'end']",
            "parameters": {
                "type": "object",
                "properties": {
                    "commands": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of CLI commands to run in order"
                    }
                },
                "required": ["commands"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "show_status",
            "description": "Get a comprehensive overview of the entire switch: system, VLANs, interface configs, LLDP, and running config all at once",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_memory",
            "description": "Save the current running configuration to flash memory (persistent)",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]

TOOL_HANDLERS = {
    "run_cli": lambda args: run_cli_command(args.get("command", "")),
    "run_cli_batch": lambda args: run_cli_commands(args.get("commands", [])),
    "show_status": lambda args: show_all_status(),
    "write_memory": lambda args: write_memory(),
}


# ---------- LLM Communication ----------

def call_ollama(messages, tools=None):
    """Call Ollama chat completions API"""
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False
    }
    if tools:
        payload["tools"] = tools

    resp = requests.post(
        f"{OLLAMA_URL}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        },
        json=payload,
        timeout=60
    )
    resp.raise_for_status()
    return resp.json()


def execute_tool_calls(tool_calls):
    """Execute tool calls from the LLM and return results.
    If a command fails, the error is included in the result so the LLM can retry."""
    results = []
    for tc in tool_calls:
        func_name = tc["function"]["name"]
        raw_args = tc["function"]["arguments"]
        if isinstance(raw_args, str):
            func_args = json.loads(raw_args)
        else:
            func_args = raw_args

        arg_summary = json.dumps(func_args)
        if len(arg_summary) > 120:
            arg_summary = arg_summary[:120] + "..."
        print(f"  [Running: {func_name}({arg_summary})]")

        handler = TOOL_HANDLERS.get(func_name)
        if handler:
            result = handler(func_args)
        else:
            result = f"Unknown function: {func_name}"

        # Check if this was an error and annotate it
        if is_error_output(result):
            result = f"COMMAND ERROR (please retry with corrected syntax): {result}"
            print(f"  [ERROR - LLM will retry]")

        display = result[:150] + "..." if len(result) > 150 else result
        print(f"  [Result: {display}]")

        results.append({
            "tool_call_id": tc["id"],
            "role": "tool",
            "name": func_name,
            "content": result
        })
    return results


def process_request(messages, tools=None, max_rounds=10):
    """Process a request that may involve multiple tool call rounds.
    The LLM can retry failed commands - each error is fed back so it
    can correct syntax and try again before giving the final answer."""
    for round_num in range(max_rounds):
        response = call_ollama(messages, tools)
        choice = response["choices"][0]
        msg = choice["message"]

        messages.append(msg)

        if msg.get("tool_calls"):
            print(f"  [Round {round_num + 1}/{max_rounds}] Running switch commands...")
            tool_results = execute_tool_calls(msg["tool_calls"])
            messages.extend(tool_results)
            continue

        content = msg.get("content", "(no response)")
        return content, messages

    return "Reached maximum tool call rounds (10). The command may need manual review.", messages


def interactive(system_prompt):
    """Interactive chat with context persistence"""
    print("=" * 60)
    print("  Aruba CX Switch AI Agent v5 - Dynamic Switch Info + Retry")
    print(f"  Model: {MODEL}")
    print(f"  Ollama: {OLLAMA_URL}")
    print("  Full switch control: show, configure, review")
    print("  Context is maintained across all messages.")
    print("  All commands logged to switch syslog (tag: AI-AGENT).")
    print("  Failed commands auto-retry with corrected syntax.")
    print("  Type 'exit' to quit, 'status' for quick overview")
    print("  Type 'clear' to reset conversation history")
    print("  Type 'log' to show recent agent log entries")
    print("  Type 'info' to show switch info gathered at startup")
    print("=" * 60)

    log_to_switch("info", "=== AI Agent v5 session started ===")

    conversation = [
        {"role": "system", "content": system_prompt}
    ]

    while True:
        try:
            user_input = input("\nYou> ")
            if user_input.lower().strip() in ("exit", "quit", "q"):
                log_to_switch("info", "=== AI Agent session ended ===")
                print("Goodbye!")
                break

            if user_input.lower().strip() == "clear":
                conversation = [{"role": "system", "content": system_prompt}]
                print("[Conversation history cleared]")
                log_to_switch("info", "Conversation history cleared by user")
                continue

            if user_input.lower().strip() == "status":
                print("\n[Getting switch status...]")
                result = show_all_status()
                print(result[:2000])
                if len(result) > 2000:
                    print(f"\n... ({len(result) - 2000} more chars)")
                conversation.append({"role": "user", "content": "Show me a quick status overview"})
                conversation.append({"role": "assistant", "content": result[:2000]})
                continue

            if user_input.lower().strip() == "log":
                print("\n[Recent agent log entries from switch syslog:]")
                log_output = subprocess.run(
                    ["vtysh", "-c", "show logging"],
                    capture_output=True, text=True, timeout=10
                ).stdout
                agent_logs = [l for l in log_output.split("\n") if "AI-AGENT" in l]
                for entry in agent_logs[-20:]:
                    print(entry)
                if not agent_logs:
                    print("(no agent log entries found)")
                continue

            if user_input.lower().strip() == "info":
                print("\n[Switch info gathered at startup:]")
                print(switch_info_summary)
                continue

            if not user_input.strip():
                continue

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
            import traceback
            traceback.print_exc()


# ---------- Startup ----------

if __name__ == "__main__":
    print("[Gathering switch information...]")
    switch_info = gather_switch_info()

    # Build a summary for the 'info' command
    switch_info_summary = (
        f"Version: {switch_info.get('version_output', '?')[:200]}\n"
        f"System: {switch_info.get('system_output', '?')[:300]}\n"
        f"Ports: {switch_info.get('port_count', '?')} "
        f"({switch_info.get('port_first', '?')} to {switch_info.get('port_last', '?')})\n"
        f"VLANs:\n{switch_info.get('vlan_output', '?')[:500]}\n"
    )
    print(switch_info_summary)

    system_prompt = build_system_prompt(switch_info)

    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
        log_to_switch("info", f"=== AI Agent v5 single query: {prompt[:200]} ===")
        conv = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        response, conv = process_request(conv, tools=TOOL_DEFINITIONS)
        print(response)
        log_to_switch("info", "=== AI Agent v5 query complete ===")
    else:
        interactive(system_prompt)