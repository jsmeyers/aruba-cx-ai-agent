#!/usr/bin/env python3
"""Run 15 CCIE-level playbook scenarios through the Aruba CX AI Agent v6.

Each scenario tests a different aspect of switch configuration and management.
Results are logged and saved to /tmp/scenario_results.md on the switch.
"""

import subprocess
import time
import sys
import json
import re
from datetime import datetime

SCENARIOS = [
    {
        "name": "01 - Baseline Discovery",
        "prompt": "Show me a complete overview of this switch: version, system info, all VLANs, port statuses, and running config. Summarize the current state.",
        "category": "discovery",
        "expected_tools": ["show_status", "run_cli"]
    },
    {
        "name": "02 - VLAN Creation and Naming",
        "prompt": "Create 3 new VLANs: VLAN 10 (DATA), VLAN 20 (VOICE), VLAN 30 (GUEST). Verify they exist after creation.",
        "category": "l2-config",
        "expected_tools": ["run_cli_batch", "run_cli"]
    },
    {
        "name": "03 - Access Port Configuration",
        "prompt": "Configure port 1/1/1 as an access port on VLAN 10, enable it, and set description 'ACCESS-PORT-DATA'. Verify the config.",
        "category": "l2-config",
        "expected_tools": ["run_cli_batch", "run_cli"]
    },
    {
        "name": "04 - Trunk Port Configuration",
        "prompt": "Configure port 1/1/2 as a trunk port allowing VLANs 10, 20, and 30. Set description 'TRUNK-UPLINK'. Verify.",
        "category": "l2-config",
        "expected_tools": ["run_cli_batch", "run_cli"]
    },
    {
        "name": "05 - Port Status Review",
        "prompt": "Show me the status of ports 1/1/1 and 1/1/2. Are they up or down? What VLANs are they assigned to?",
        "category": "discovery",
        "expected_tools": ["run_cli"]
    },
    {
        "name": "06 - LLDP Neighbor Discovery",
        "prompt": "Show LLDP neighbor information. Are there any neighbors? If the command fails, try alternate syntax.",
        "category": "discovery",
        "expected_tools": ["show_lldp", "run_cli"]
    },
    {
        "name": "07 - Spanning Tree Configuration",
        "prompt": "Enable MSTP spanning tree mode and set the switch priority to 4096. Show the spanning tree status after configuration.",
        "category": "l2-config",
        "expected_tools": ["run_cli_batch", "run_cli"]
    },
    {
        "name": "08 - Interface Error Check",
        "prompt": "Check all interfaces for errors, CRC, drops, or runts. List any ports with non-zero error counters.",
        "category": "monitoring",
        "expected_tools": ["run_cli"]
    },
    {
        "name": "09 - Configuration Review and Audit",
        "prompt": "Review the full running configuration. Identify any security issues, misconfigurations, or best practice violations.",
        "category": "audit",
        "expected_tools": ["run_cli"]
    },
    {
        "name": "10 - Multi-VLAN Port Assignment",
        "prompt": "Configure ports 1/1/3 through 1/1/5 as access ports: 1/1/3 on VLAN 10, 1/1/4 on VLAN 20, 1/1/5 on VLAN 30. Enable all ports. Verify each one.",
        "category": "l2-config",
        "expected_tools": ["run_cli_batch", "run_cli"]
    },
    {
        "name": "11 - MAC Address Table Analysis",
        "prompt": "Show the MAC address table. Are there any learned MACs? Which ports and VLANs do they belong to?",
        "category": "discovery",
        "expected_tools": ["run_cli"]
    },
    {
        "name": "12 - Log Anomaly Detection",
        "prompt": "Show the switch logs and identify any errors, warnings, or anomalies. Ignore routine DHCP and NAE messages.",
        "category": "monitoring",
        "expected_tools": ["run_cli"]
    },
    {
        "name": "13 - Configuration Save and Verify",
        "prompt": "Save the configuration to flash memory. Then show the running config to verify it was saved correctly.",
        "category": "system",
        "expected_tools": ["write_memory", "run_cli"]
    },
    {
        "name": "14 - Bulk Port Description",
        "prompt": "Set descriptions on ports 1/1/6 through 1/1/10. Name them ACCESS-06 through ACCESS-10. Verify all descriptions are set.",
        "category": "l2-config",
        "expected_tools": ["run_cli_batch", "run_cli"]
    },
    {
        "name": "15 - Final State Summary",
        "prompt": "Give me a complete summary of the switch state: how many VLANs, how many ports up vs down, which ports have descriptions, and any issues you've found across this session.",
        "category": "audit",
        "expected_tools": ["show_status", "run_cli"]
    },
]

def run_scenario(agent_path, scenario, timeout=90):
    """Run a single scenario and capture results."""
    name = scenario["name"]
    prompt = scenario["prompt"]
    print(f"\n{'='*60}")
    print(f"SCENARIO: {name}")
    print(f"PROMPT: {prompt}")
    print(f"{'='*60}")

    start = time.time()
    try:
        import os
        env = os.environ.copy()
        result = subprocess.run(
            ["python3", agent_path, prompt],
            capture_output=True, text=True, timeout=timeout,
            cwd="/tmp", env=env
        )
        elapsed = time.time() - start
        output = result.stdout
        error = result.stderr

        # Check if it succeeded
        success = "Error:" not in output[:200] and "SECURITY:" not in output[:200]

        # Count tool calls
        tool_calls = output.count("[Running:")

        # Check for error indicators
        has_errors = "COMMAND ERROR" in output
        has_retries = "will retry" in output

        result_data = {
            "scenario": name,
            "category": scenario["category"],
            "prompt": prompt,
            "success": success,
            "elapsed_seconds": round(elapsed, 1),
            "tool_calls": tool_calls,
            "had_errors": has_errors,
            "had_retries": has_retries,
            "output_length": len(output),
            "output_preview": output[:500],
            "error": error[:500] if error else None
        }

        status = "PASS" if success else "FAIL"
        print(f"  Status: {status}")
        print(f"  Time: {elapsed:.1f}s")
        print(f"  Tool calls: {tool_calls}")
        print(f"  Had errors: {has_errors}")
        print(f"  Had retries: {had_retries}")
        print(f"  Output preview: {output[:200]}...")

        return result_data

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"  TIMEOUT after {elapsed:.1f}s")
        return {
            "scenario": name,
            "category": scenario["category"],
            "success": False,
            "elapsed_seconds": round(elapsed, 1),
            "tool_calls": 0,
            "had_errors": True,
            "had_retries": False,
            "error": "Timeout",
            "output_preview": "",
            "output_length": 0
        }
    except Exception as e:
        elapsed = time.time() - start
        print(f"  EXCEPTION: {e}")
        return {
            "scenario": name,
            "category": scenario["category"],
            "success": False,
            "elapsed_seconds": round(elapsed, 1),
            "tool_calls": 0,
            "had_errors": True,
            "had_retries": False,
            "error": str(e),
            "output_preview": "",
            "output_length": 0
        }

def main():
    agent_path = "/tmp/agent_v6.py"
    results = []

    print(f"Starting 15 CCIE-Level Playbook Scenarios")
    print(f"Agent: {agent_path}")
    print(f"Time: {datetime.utcnow().isoformat()}")

    for i, scenario in enumerate(SCENARIOS):
        result = run_scenario(agent_path, scenario)
        results.append(result)
        time.sleep(2)  # Brief pause between scenarios

    # Generate summary report
    print(f"\n\n{'='*60}")
    print("SCENARIO RESULTS SUMMARY")
    print(f"{'='*60}")

    passed = sum(1 for r in results if r["success"])
    failed = sum(1 for r in results if not r["success"])
    total_tool_calls = sum(r["tool_calls"] for r in results)
    total_errors = sum(1 for r in results if r["had_errors"])
    total_retries = sum(1 for r in results if r["had_retries"])
    total_time = sum(r["elapsed_seconds"] for r in results)

    print(f"Total: {len(results)} scenarios")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Total tool calls: {total_tool_calls}")
    print(f"Scenarios with errors: {total_errors}")
    print(f"Scenarios with retries: {total_retries}")
    print(f"Total time: {total_time:.1f}s")

    print(f"\n{'Scenario':<40} {'Status':<8} {'Time':<8} {'Tools':<6} {'Errors':<7} {'Retries':<8}")
    print("-" * 80)
    for r in results:
        status = "PASS" if r["success"] else "FAIL"
        print(f"{r['scenario']:<40} {status:<8} {r['elapsed_seconds']:<8.1f} {r['tool_calls']:<6} {str(r['had_errors']):<7} {str(r['had_retries']):<8}")

    # Save results
    with open("/tmp/scenario_results.md", "w") as f:
        f.write(f"# 15 Scenario Playbook Results\n\n")
        f.write(f"Date: {datetime.utcnow().isoformat()}\n")
        f.write(f"Agent: agent_v6.py\n\n")
        f.write(f"## Summary\n\n")
        f.write(f"| Metric | Value |\n|--------|-------|\n")
        f.write(f"| Total scenarios | {len(results)} |\n")
        f.write(f"| Passed | {passed} |\n")
        f.write(f"| Failed | {failed} |\n")
        f.write(f"| Total tool calls | {total_tool_calls} |\n")
        f.write(f"| Scenarios with errors | {total_errors} |\n")
        f.write(f"| Scenarios with retries | {total_retries} |\n")
        f.write(f"| Total time | {total_time:.1f}s |\n\n")
        f.write(f"## Detailed Results\n\n")
        for r in results:
            f.write(f"### {r['scenario']}\n\n")
            f.write(f"- Status: {'PASS' if r['success'] else 'FAIL'}\n")
            f.write(f"- Category: {r['category']}\n")
            f.write(f"- Time: {r['elapsed_seconds']}s\n")
            f.write(f"- Tool calls: {r['tool_calls']}\n")
            f.write(f"- Had errors: {r['had_errors']}\n")
            f.write(f"- Had retries: {r['had_retries']}\n")
            if r.get("output_preview"):
                f.write(f"- Output: {r['output_preview'][:200]}...\n")
            if r.get("error"):
                f.write(f"- Error: {r['error'][:200]}\n")
            f.write("\n")

    print(f"\nResults saved to /tmp/scenario_results.md")

if __name__ == "__main__":
    main()