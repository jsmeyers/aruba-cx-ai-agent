# Aruba CX Switch AI Agent

An AI agent that runs on the Linux subsystem of an Aruba CX network switch, connecting to an Ollama LLM server for natural language switch management.

## What This Does

- Downloads and boots an Aruba CX switch simulator (OVA) in QEMU/KVM
- Accesses the switch's Linux subsystem via `start-shell`
- Deploys a Python AI agent that uses Ollama for LLM inference
- Agent can show/configure ports, VLANs, review configs, and analyze logs
- Scheduled monitoring runs every 15 minutes for port status, LLDP, log anomalies, etc.
- All agent commands are logged to the switch syslog

## Requirements

- Linux host with KVM support (`/dev/kvm`)
- 4GB+ RAM, 20GB+ free disk
- Python 3, QEMU, tmux, asciinema
- Access to an Ollama server (configure your own - see Configuration section below)

## Quick Start

### 1. Install Dependencies

```bash
sudo apt install -y qemu-system-x86 qemu-utils asciinema tmux sshpass python3-pip
pip3 install gdown --break-system-packages
```

### 2. Download the Aruba CX OVA

```bash
cd ~/aruba-cx-agent-setup
gdown --folder "https://drive.google.com/drive/folders/1s26RdIueJPQpNeDnN3-JhDxUt2MFOL6V"
```

This downloads the ArubaOS-CX 10.07.0010 simulator OVA (~532MB).

### 3. Extract and Convert to QEMU Format

```bash
mkdir -p ova-extracted && cd ova-extracted
tar xf "../Auba Simulator/ArubaOS-CX_10_07_0010.ova"
cd ..
qemu-img convert -f vmdk -O qcow2 \
  ova-extracted/arubaoscx-disk-image-genericx86-p4-20210610000730.vmdk \
  aruba-cx.qcow2
```

### 4. Boot the Switch VM

```bash
tmux new-session -d -s aruba -x 120 -y 40 \
  'asciinema rec --overwrite session.cast -c \
  "qemu-system-x86_64 -enable-kvm -m 4096 -smp 2 \
   -drive file=aruba-cx.qcow2,if=ide,format=qcow2 \
   -netdev user,id=net0,hostfwd=tcp::2222-:22,hostfwd=tcp::8443-:443 \
   -device e1000,netdev=net0 -nographic -serial mon:stdio"'
sleep 30
tmux capture-pane -t aruba -p
```

### 5. Login and Configure

```bash
# Login: admin / (empty password, set new password when prompted)
tmux send-keys -t aruba 'admin' Enter
sleep 3
tmux send-keys -t aruba '' Enter
sleep 3
tmux send-keys -t aruba 'YourPassword123!' Enter
sleep 2
tmux send-keys -t aruba 'YourPassword123!' Enter
sleep 3

# Enable SSH and REST API
tmux send-keys -t aruba 'configure terminal' Enter
tmux send-keys -t aruba 'ssh server vrf default' Enter
tmux send-keys -t aruba 'https-server rest access-mode read-write' Enter
tmux send-keys -t aruba 'https-server vrf default' Enter
tmux send-keys -t aruba 'exit' Enter
```

### 6. Access Linux Shell and Deploy Agent

```bash
# Enter Linux subsystem
tmux send-keys -t aruba 'start-shell' Enter
sleep 2

# Transfer agent_v5.py to the switch (base64 method - SCP not supported)
# See deploy script or use the helper below
B64=$(base64 -w0 agent_v5.py)
# Send in 400-char chunks via tmux, then decode on switch:
# echo -n '<chunk>' > /tmp/agent_v5_b64.txt  (first chunk)
# echo -n '<chunk>' >> /tmp/agent_v5_b64.txt (subsequent chunks)
# base64 -d /tmp/agent_v5_b64.txt > /tmp/agent_v5.py && chmod +x /tmp/agent_v5.py
```

### 7. Run the Agent

```bash
# On the switch (via tmux):
/tmp/start_agent.sh

# Or directly:
python3 /tmp/agent_v5.py

# Single query:
python3 /tmp/agent_v5.py "Show me all port statuses"

# Interactive mode with context:
python3 /tmp/agent_v5.py
# > Show me which interfaces have descriptions
# > Now set a description on port 1/1/1
# > Save the config
# (context is maintained across turns)
```

### 8. Set Up Recurring Monitoring

```bash
# Deploy monitor.py to the switch (same base64 method as agent)

# Install systemd service and timer on the switch:
sudo cp ai-monitor.service /etc/systemd/system/
sudo cp ai-monitor.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ai-monitor.timer

# Check timer status:
SYSTEMD_PAGER=cat systemctl list-timers ai-monitor.timer

# View monitor logs:
grep AI-MONITOR /var/log/messages | tail -20

# View latest report:
cat /tmp/monitor_report.txt
```

## Configuration

You must configure the Ollama endpoint before use. Edit the following settings at the top of each Python file (`agent_v5.py`, `monitor.py`, etc.):

| Setting | Default Placeholder | Description |
|---------|---------------------|-------------|
| `OLLAMA_URL` | `http://YOUR_OLLAMA_SERVER:11434` | Your Ollama API endpoint (replace with your server IP/hostname) |
| `API_KEY` | `your-api-key` | API key (any string works with Ollama by default) |
| `MODEL` | `glm-5.2:cloud` | LLM model name (must match a model available on your Ollama server) |

**Before running the agent, replace `YOUR_OLLAMA_SERVER` with your actual Ollama server IP or hostname.**

You can verify available models on your Ollama server:
```bash
curl http://YOUR_OLLAMA_SERVER:11434/api/tags
```

The switch password placeholder `YourPassword123!` should also be replaced with your actual switch admin password.

## Files

| File | Description |
|------|-------------|
| `agent_v5.py` | Latest AI agent (dynamic switch info, tool calling, context memory, logging) |
| `agent.py` | v1 - basic chat only (no switch control) |
| `agent_v2.py` | v2 - adds switch control via vtysh |
| `agent_v3.py` | v3 - adds context memory across turns |
| `agent_v4.py` | v4 - adds syslog command logging |
| `monitor.py` | Scheduled monitoring script (ports, LLDP, logs, VLANs, errors) |
| `start_agent.sh` | Shell launcher (points to agent_v5.py) |
| `ai-monitor.service` | systemd service for monitoring |
| `ai-monitor.timer` | 15-minute recurring timer |
| `setup_notes.md` | Detailed step-by-step setup guide |
| `.gitignore` | Excludes OVA images, qcow2 disks, recordings |

## Agent Features (v5)

- **Dynamic switch info**: Gathers version, model, port count, VLANs at startup
- **Switch CLI control**: Run any command via `vtysh` (show, configure, etc.)
- **Context memory**: Full conversation history maintained across turns
- **Error retry**: Failed commands are fed back to the LLM for self-correction
- **Syslog logging**: All commands logged with tag `AI-AGENT`
- **Up-arrow history**: readline support for command history in interactive mode

## Agent Commands (Interactive Mode)

| Command | Description |
|---------|-------------|
| `status` | Quick switch overview |
| `info` | Show switch info gathered at startup |
| `log` | Show recent agent log entries |
| `clear` | Reset conversation history |
| `exit` | Quit the agent |

## Monitoring Checks

| Check | What It Does |
|-------|-------------|
| Ports | Status of all 52 ports, LLM analyzes for issues |
| LLDP | Neighbor changes (new/missing since last run) |
| Logs | Error/critical entries, LLM analyzes for anomalies |
| VLANs | Down VLANs, unexpected VLANs, naming issues |
| Interface Errors | CRC, drops, runts, giants on all ports |

## Useful Commands

```bash
# Attach to switch console
tmux attach -t aruba

# SSH to switch (port 2222 on host)
ssh -p 2222 admin@127.0.0.1  # password: YourPassword123!

# View asciinema recording
asciinema play session.cast

# Run individual monitoring checks
python3 /tmp/monitor.py --check ports
python3 /tmp/monitor.py --check lldp
python3 /tmp/monitor.py --check logs
python3 /tmp/monitor.py --check vlans
python3 /tmp/monitor.py --check errors

# View agent logs on switch
grep AI-AGENT /var/log/messages | tail -20
grep AI-MONITOR /var/log/messages | tail -20

# Check systemd timer
SYSTEMD_PAGER=cat systemctl list-timers ai-monitor.timer
```

## How It Works

1. Agent starts, queries switch for version, ports, VLANs via `vtysh -c "show ..."`
2. Switch info is injected into the LLM system prompt
3. User asks a question in natural language
4. LLM decides which CLI commands to run (tool calling)
5. Agent executes commands via `vtysh -c "<command>"`
6. Results fed back to LLM for analysis
7. LLM provides final response or runs more commands (up to 10 rounds)
8. All commands logged to switch syslog via `logger -t AI-AGENT`

## License

This project is for educational/lab purposes. The ArubaOS-CX OVA is provided by HPE Aruba Networking for training use only.