#!/bin/sh
# Aruba CX AI Agent Launcher
# Run this from the Linux shell (start-shell) to start the AI agent
# Usage: /tmp/start_agent.sh [options or prompt]
#
# Uses agent_v5.py - the latest agent with:
#   - Dynamic switch info gathering at startup
#   - Full switch CLI control via vtysh (show, configure, review)
#   - Context memory across conversation turns
#   - Error detection and automatic retry
#   - Command logging to switch syslog (tag: AI-AGENT)
#   - Up-arrow history via readline
#
# Interactive mode:
#   /tmp/start_agent.sh
#
# Single query:
#   /tmp/start_agent.sh "Show me the status of all ports"

echo "Starting Aruba CX AI Agent v5..."
python3 /tmp/agent_v5.py "$@"