#!/bin/sh
# Aruba CX AI Agent Launcher v7
# Deployed to /opt/ai-agent/ for security (not /tmp)
#
# Environment variables (set in /opt/ai-agent/agent.env):
#   OLLAMA_URL       - Your Ollama server URL (https:// recommended)
#   OLLAMA_API_KEY   - API key for Ollama
#   OLLAMA_MODEL     - Model name (default: glm-5.2:cloud)
#   OLLAMA_CA_CERT   - Path to CA cert for TLS pinning (optional)
#   AGENT_AUTH_KEY   - Shared secret for agent access (optional)
#   AGENT_MODE       - "readonly" for read-only mode (optional)
#
# Usage:
#   /opt/ai-agent/start_agent.sh              # Interactive mode
#   /opt/ai-agent/start_agent.sh "show vlan"  # Single query
#   /opt/ai-agent/start_agent.sh --read-only  # Read-only interactive mode

# Load environment config if it exists
if [ -f /opt/ai-agent/agent.env ]; then
  . /opt/ai-agent/agent.env
fi

echo "Starting Aruba CX AI Agent v7..."
python3 /opt/ai-agent/agent_v7.py "$@"