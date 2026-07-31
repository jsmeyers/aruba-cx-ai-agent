#!/usr/bin/env python3
"""Aruba CX Switch AI Agent v2 - With Switch Control

This agent can:
- Chat with an LLM via Ollama
- Execute switch CLI commands (show/configure) via vtysh
- Read and analyze switch configuration
- Configure ports, VLANs, and other switch features
"""

import requests
import json
import subprocess
import sys
import re

OLLAMA_URL = "http://YOUR_OLLAMA_SERVER:11434"
API_KEY = "your-api-key"
MODEL = "glm-5.2:cloud"

SYSTEM_PROMPT = """You are an AI assistant running on an Aruba CX network switch (ArubaOS-CX 10.07.0010).
You have access to switch CLI commands through the run_command function.

Available show commands:
- show version, show system, show interface, show interface <port>
- show vlan, show running-config, show running-config interface <port>
- show ip route, show mac-address-table, show lldp info, show spanning-tree
- show vlan <id>, show port-access <opts>
- show interface <port> - shows detailed port status

Configuration is done via:
- configure terminal -> interface <port> -> commands
- configure terminal -> vlan <id> -> commands

Always use run_command to check the current state before making changes.
After making changes, verify with show commands and optionally write memory.

Port naming format: 1/1/1, 1/1/2, etc. (slot/module/port)
VLAN creation: configure terminal -> vlan <id> -> name <name> -> exit

Keep responses concise and actionable."""

# ---------- Switch CLI Interface ----------

def run_cli_command(command):
    """Run a single vtysh CLI command and return output"""
    try:
        result = subprocess.run(
            ["vtysh", "-c", command],
            capture_output=True, text=True, timeout=15
        )
        output = result.stdout + result.stderr
        return output.strip() if output.strip() else "(no output)"
    except Exception as e:
        return f"Error: {e}"


def run_cli_commands(commands):
    """Run multiple vtysh CLI commands (for config sequences)"""
    args = []
    for cmd in commands:
        args.extend(["-c", cmd])
    try:
        result = subprocess.run(
            ["vtysh"] + args,
            capture_output=True, text=True, timeout=20
        )
        output = result.stdout + result.stderr
        return output.strip() if output.strip() else "(no output)"
    except Exception as e:
        return f"Error: {e}"


def show_interface(port=None):
    """Show interface status"""
    if port:
        return run_cli_command(f"show interface {port}")
    return run_cli_command("show interface")


def show_vlan(vlan_id=None):
    """Show VLAN information"""
    if vlan_id:
        return run_cli_command(f"show vlan {vlan_id}")
    return run_cli_command("show vlan")


def show_running_config(scope=None):
    """Show running configuration"""
    if scope:
        return run_cli_command(f"show running-config {scope}")
    return run_cli_command("show running-config")


def show_system():
    """Show system info"""
    return run_cli_command("show system")


def show_version():
    """Show version info"""
    return run_cli_command("show version")


def show_lldp():
    """Show LLDP neighbors"""
    return run_cli_command("show lldp info remote-device")


def show_mac_table():
    """Show MAC address table"""
    return run_cli_command("show mac-address-table")


def show_spanning_tree():
    """Show spanning tree info"""
    return run_cli_command("show spanning-tree")


def show_ip_route():
    """Show IP routing table"""
    return run_cli_command("show ip route")


def show_port_status():
    """Show brief port status for all ports"""
    return run_cli_command("show interface")


def configure_port(port, commands):
    """Configure a switch port.
    commands is a list of config commands to apply under interface <port>.
    Example: configure_port("1/1/1", ["no shutdown", "vlan access 10"])
    """
    full_cmds = ["configure terminal", f"interface {port}"] + commands + ["exit", "end"]
    return run_cli_commands(full_cmds)


def configure_vlan(vlan_id, name=None, commands=None):
    """Create/configure a VLAN.
    Example: configure_vlan(20, "DATA_VLAN")
    """
    cmds = ["configure terminal", f"vlan {vlan_id}"]
    if name:
        cmds.append(f"name {name}")
    if commands:
        cmds.extend(commands)
    cmds.extend(["exit", "end"])
    return run_cli_commands(cmds)


def assign_port_to_vlan(port, vlan_id, mode="access"):
    """Assign a port to a VLAN"""
    if mode == "access":
        return configure_port(port, [
            f"vlan access {vlan_id}"
        ])
    elif mode == "trunk":
        return configure_port(port, [
            "no routing",
            f"vlan trunk allowed {vlan_id}"
        ])


def write_memory():
    """Save configuration"""
    return run_cli_command("write memory")


def show_all_status():
    """Get a comprehensive overview of the switch"""
    output = "=== SYSTEM ===\n"
    output += show_system() + "\n\n"
    output += "=== VLANS ===\n"
    output += show_vlan() + "\n\n"
    output += "=== INTERFACES (summary) ===\n"
    output += show_interface() + "\n\n"
    output += "=== LLDP NEIGHBORS ===\n"
    output += show_lldp() + "\n\n"
    output += "=== RUNNING CONFIG ===\n"
    output += show_running_config() + "\n"
    return output


# ---------- LLM Chat with Tool Support ----------

# Available tools for the LLM
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "run_cli",
            "description": "Run any Aruba CX switch CLI command (show, configure, etc.)",
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
            "description": "Run multiple CLI commands in sequence (use for configuration). Pass commands in order.",
            "parameters": {
                "type": "object",
                "properties": {
                    "commands": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of commands to run in sequence, e.g. ['configure terminal', 'vlan 30', 'name DATA', 'exit', 'end']"
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
            "description": "Get a comprehensive overview of the switch: system, VLANs, interfaces, LLDP, running config",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "configure_port",
            "description": "Configure a switch port with specific settings",
            "parameters": {
                "type": "object",
                "properties": {
                    "port": {"type": "string", "description": "Port name, e.g. '1/1/1'"},
                    "commands": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Config commands for the port, e.g. ['no shutdown', 'vlan access 10']"
                    }
                },
                "required": ["port", "commands"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_memory",
            "description": "Save the current configuration to flash memory",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]

# Map function names to actual implementations
TOOL_HANDLERS = {
    "run_cli": lambda args: run_cli_command(args.get("command", "")),
    "run_cli_batch": lambda args: run_cli_commands(args.get("commands", [])),
    "show_status": lambda args: show_all_status(),
    "configure_port": lambda args: configure_port(args.get("port", ""), args.get("commands", [])),
    "write_memory": lambda args: write_memory(),
}


def call_ollama(messages, tools=None):
    """Call Ollama chat completions API with optional tool support"""
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
    """Execute tool calls from the LLM and return results"""
    results = []
    for tc in tool_calls:
        func_name = tc["function"]["name"]
        func_args = json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"]

        print(f"  [Executing: {func_name}({json.dumps(func_args)[:120]})]")
        handler = TOOL_HANDLERS.get(func_name)
        if handler:
            result = handler(func_args)
        else:
            result = f"Unknown function: {func_name}"
        print(f"  [Result: {result[:200]}{'...' if len(result) > 200 else ''}]")
        results.append({
            "tool_call_id": tc["id"],
            "role": "tool",
            "name": func_name,
            "content": result
        })
    return results


def chat_with_tools(user_prompt):
    """Send a prompt to the LLM, handle tool calls, and return final response"""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt}
    ]

    max_rounds = 10  # Prevent infinite tool call loops

    for round_num in range(max_rounds):
        response = call_ollama(messages, tools=TOOL_DEFINITIONS)
        choice = response["choices"][0]
        msg = choice["message"]

        # Add assistant message to conversation
        messages.append(msg)

        # Check if there are tool calls to execute
        if msg.get("tool_calls"):
            print(f"\n[Round {round_num + 1}] Agent is running switch commands...")
            tool_results = execute_tool_calls(msg["tool_calls"])
            messages.extend(tool_results)
            continue

        # No tool calls - this is the final response
        content = msg.get("content", "(no response)")
        return content

    return "Reached maximum tool call rounds. Last response may be incomplete."


def interactive():
    print("=" * 60)
    print("  Aruba CX Switch AI Agent v2 - With Switch Control")
    print(f"  Model: {MODEL}")
    print(f"  Ollama: {OLLAMA_URL}")
    print("  The agent can: show status, configure ports/VLANs,")
    print("  review configs, and run any CLI command.")
    print("  Type 'exit' to quit, 'status' for quick overview")
    print("=" * 60)

    while True:
        try:
            user_input = input("\nYou> ")
            if user_input.lower() in ("exit", "quit", "q"):
                print("Goodbye!")
                break

            if user_input.lower().strip() == "status":
                print("\n[Getting switch status...]")
                result = show_all_status()
                print(result[:2000])
                if len(result) > 2000:
                    print(f"\n... ({len(result) - 2000} more chars, use agent to query specific details)")
                continue

            print("\nAgent> ", end="", flush=True)
            response = chat_with_tools(user_input)
            print(response)

        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
        response = chat_with_tools(prompt)
        print(response)
    else:
        interactive()