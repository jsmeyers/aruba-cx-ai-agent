#!/usr/bin/env python3
"""Aruba CX Switch AI Agent v3 - With Switch Control + Context Memory

Features:
- Maintains full conversation history across turns (context awareness)
- LLM can call functions to interact with the switch via vtysh
- Can show/configure ports, VLANs, running config, etc.
- Can review and analyze switch configurations
- Interactive and single-command modes
"""

import requests
import json
import subprocess
import sys
import os

OLLAMA_URL = "http://YOUR_OLLAMA_SERVER:11434"
API_KEY = "your-api-key"
MODEL = "glm-5.2:cloud"

SYSTEM_PROMPT = """You are an AI assistant running on an Aruba CX network switch (ArubaOS-CX 10.07.0010).
You have access to switch CLI commands through the run_cli and run_cli_batch functions.

IMPORTANT COMMAND REFERENCE:
- show running-config                 -> full config (use grep to filter)
- show running-config interface       -> all interface configs (shows descriptions!)
- show interface                      -> brief status of all ports
- show interface 1/1/1               -> detailed status of port 1/1/1
- show running-config interface 1/1/1 -> config of a specific port
- show vlan                           -> VLAN summary
- show vlan 100                       -> VLAN 100 details
- show system                         -> system info
- show version                        -> version info
- show lldp info remote-device       -> LLDP neighbors
- show mac-address-table              -> MAC table
- show spanning-tree                   -> STP info
- show ip route                        -> routing table

CONFIGURATION EXAMPLES (use run_cli_batch with multiple commands):
- Create VLAN: ["configure terminal", "vlan 100", "name MGMT", "exit", "end"]
- Enable port: ["configure terminal", "interface 1/1/1", "no shutdown", "exit", "end"]
- Set description: ["configure terminal", "interface 1/1/1", "description UPLINK-CORE", "exit", "end"]
- Access VLAN: ["configure terminal", "interface 1/1/1", "no routing", "vlan access 100", "exit", "end"]
- Trunk VLAN: ["configure terminal", "interface 1/1/1", "no routing", "vlan trunk allowed 100,200", "exit", "end"]
- Save config: write_memory() or ["write memory"]

NOTES:
- Port names: 1/1/1 through 1/1/52
- To find interfaces with descriptions: run_cli("show running-config interface") and look for "description" lines
- Each interface config block shows: interface 1/1/X then indented commands then exit
- Always check current state before making changes.
- After making changes, verify with show commands and save with write_memory.
- Keep responses concise and well-formatted."""

# ---------- Switch CLI Interface ----------

def run_cli_command(command):
    """Run a single vtysh CLI command and return output"""
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


def run_cli_commands(commands):
    """Run multiple vtysh CLI commands in sequence"""
    args = []
    for cmd in commands:
        args.extend(["-c", cmd])
    try:
        result = subprocess.run(
            ["vtysh"] + args,
            capture_output=True, text=True, timeout=20
        )
        output = result.stdout + result.stderr
        return output.strip() if output.strip() else "(no output - commands succeeded)"
    except subprocess.TimeoutExpired:
        return "Error: commands timed out"
    except Exception as e:
        return f"Error: {e}"


def write_memory():
    """Save configuration"""
    return run_cli_command("write memory")


def show_all_status():
    """Get a comprehensive overview of the switch"""
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
            "description": "Run any Aruba CX switch CLI command. Examples: 'show interface 1/1/1', 'show vlan', 'show running-config interface', 'show running-config', etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The CLI command to run"
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
    """Execute tool calls from the LLM and return results"""
    results = []
    for tc in tool_calls:
        func_name = tc["function"]["name"]
        raw_args = tc["function"]["arguments"]
        if isinstance(raw_args, str):
            func_args = json.loads(raw_args)
        else:
            func_args = raw_args

        # Show what the agent is doing
        arg_summary = json.dumps(func_args)
        if len(arg_summary) > 120:
            arg_summary = arg_summary[:120] + "..."
        print(f"  [Running: {func_name}({arg_summary})]")

        handler = TOOL_HANDLERS.get(func_name)
        if handler:
            result = handler(func_args)
        else:
            result = f"Unknown function: {func_name}"

        # Truncate very large outputs for display
        display = result[:150] + "..." if len(result) > 150 else result
        print(f"  [Result: {display}]")

        results.append({
            "tool_call_id": tc["id"],
            "role": "tool",
            "name": func_name,
            "content": result
        })
    return results


def process_request(messages, tools=None):
    """Process a request that may involve multiple tool call rounds.
    Returns the assistant's final text response and updated messages list."""
    max_rounds = 10

    for round_num in range(max_rounds):
        response = call_ollama(messages, tools)
        choice = response["choices"][0]
        msg = choice["message"]

        # Add the assistant message (with any tool calls) to history
        messages.append(msg)

        if msg.get("tool_calls"):
            print(f"  [Round {round_num + 1}] Running switch commands...")
            tool_results = execute_tool_calls(msg["tool_calls"])
            messages.extend(tool_results)
            continue

        # No tool calls - final response
        content = msg.get("content", "(no response)")
        return content, messages

    return "Reached maximum tool call rounds.", messages


def interactive():
    """Interactive chat with context persistence"""
    print("=" * 60)
    print("  Aruba CX Switch AI Agent v3 - With Context Memory")
    print(f"  Model: {MODEL}")
    print(f"  Ollama: {OLLAMA_URL}")
    print("  Full switch control: show, configure, review")
    print("  Context is maintained across all messages.")
    print("  Type 'exit' to quit, 'status' for quick overview")
    print("  Type 'clear' to reset conversation history")
    print("=" * 60)

    # Maintain full conversation history across turns
    conversation = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]

    while True:
        try:
            user_input = input("\nYou> ")
            if user_input.lower().strip() in ("exit", "quit", "q"):
                print("Goodbye!")
                break

            if user_input.lower().strip() == "clear":
                conversation = [{"role": "system", "content": SYSTEM_PROMPT}]
                print("[Conversation history cleared]")
                continue

            if user_input.lower().strip() == "status":
                print("\n[Getting switch status...]")
                result = show_all_status()
                print(result[:2000])
                if len(result) > 2000:
                    print(f"\n... ({len(result) - 2000} more chars)")
                # Add to context so agent knows what we showed
                conversation.append({"role": "user", "content": "Show me a quick status overview"})
                conversation.append({"role": "assistant", "content": result[:2000]})
                continue

            if not user_input.strip():
                continue

            # Add user message to persistent conversation
            conversation.append({"role": "user", "content": user_input})

            print("\nAgent> ", end="", flush=True)
            response, conversation = process_request(conversation, tools=TOOL_DEFINITIONS)
            print(response)

        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
        conv = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]
        response, conv = process_request(conv, tools=TOOL_DEFINITIONS)
        print(response)
    else:
        interactive()