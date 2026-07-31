# Aruba CX Switch - AI Agent Setup Notes

## Project: Configure ArubaCX Switch Linux Subsystem with AI Agent

**Date:** 2026-07-31
**Goal:** Download Aruba CX OVA, boot it, access Linux subsystem, install a basic AI agent that connects to Ollama at YOUR_OLLAMA_SERVER:11434 using model glm-5.2:cloud
**Status:** SUCCESS

---

## Environment

- Host: Linux 6.14.0-37-generic, x86_64, 8 cores, 16GB RAM, 468GB disk (424GB free)
- KVM: available (/dev/kvm present)
- Virtualization: QEMU/KVM 8.2.2
- Screen recording: asciinema (terminal session recording saved to session.cast)
- Virtual switch: ArubaOS-CX Virtual.10.07.0010 (ArubaCX OVA simulator)

## Key Findings from Research

### OVA Image Source
- Official: HPE Aruba Networking Support Portal (requires ASP account)
  - https://networkingsupport.hpe.com/downloads;fileTypes=SOFTWARE;products=Aruba%20Switches;softwareMajorVersions=10.15
  - Requires Aruba Support Portal (ASP) account with company association
- Community-shared: Google Drive folder (ArubaOS-CX_101_07_0010 Simulator)
  - https://drive.google.com/drive/folders/1s26RdIueJPQpNeDnN3-JhDxUt2MFOL6V
- GNS3 marketplace appliance file: https://www.gns3.com/marketplace/appliance/arubaos-cx-simulation-software

### Linux Subsystem (start-shell)
- AOS-CX provides access to underlying Linux via `start-shell` command from CLI
- By default, security mode is "standard" which allows start-shell access
- "enhanced" secure mode disables start-shell (requires zeroization to change)
- From CLI: `start-shell` drops to bash as user `admin` (uid=1001)
- From ServiceOS: `sudo bash` for root
- Warning: accessing Linux shell sets an SE flag that can only be reset via zeroization
- The user is `admin`, NOT root - limited permissions

### Switch Linux Environment
- OS: Yocto-based Linux (ArubaOS-CX), kernel 4.19.68-yocto-standard
- Python: 3.7.4 (with `requests` library v2.22.0 pre-installed)
- Tools available: python3, python, curl, wget
- No pip available (but not needed since requests is pre-installed)
- User: admin (uid=1001, gid=1022 administrators)
- Shell: bash

### REST API
- Enable: `https-server rest access-mode read-write`
- Bind to VRF: `https-server vrf default` and `https-server vrf mgmt`
- Login: POST to `https://<ip>/rest/latest/login?username=admin&password=<pass>`
- Note: On the 10.07 simulator, REST API returned 401 - may need additional config
- Python library: `pyaoscx` (pip installable, Python 3 only)

### Ollama Server (YOUR_OLLAMA_SERVER:11434)
- Reachable from the switch VM via QEMU user-mode networking
- Available models include: glm-5.2:cloud, glm-5:cloud, glm-4.7:cloud, gemma4:cloud, and many others
- The /v1/chat/completions endpoint works with the OpenAI-compatible API format

---

## Step-by-Step Setup Process (COMPLETED)

### Step 1: Install Virtualization Tools (QEMU/KVM)

```bash
sudo apt update
sudo apt install -y qemu-system-x86 qemu-utils ovmf bridge-utils \
  libvirt-daemon-system libvirt-clients virtinst asciinema tmux sshpass python3-pip
pip3 install gdown --break-system-packages
```

**Status:** DONE
**Verification:** `qemu-system-x86_64 --version` returns 8.2.2

### Step 2: Download Aruba CX OVA Image

Used gdown to download from the community Google Drive folder:

```bash
cd ~/aruba-cx-agent-setup
gdown --folder "https://drive.google.com/drive/folders/1s26RdIueJPQpNeDnN3-JhDxUt2MFOL6V"
```

This downloads to `~/aruba-cx-agent-setup/Auba Simulator/`:
- Aruba_AOS-CX_Switch_Simulator_10_07_0010_ova.zip (520MB)
- ArubaOS-CX_10_07_0010.ova (532MB)
- ArubaOS-CX_10_07_0010.ova.sig
- ArubaOS-CX_OVA_ALA.pdf

**Status:** DONE
**Note:** The OVA file itself (not the zip) is the one we use directly

### Step 3: Extract and Convert OVA to QEMU Format

OVA is a tar archive containing OVF + VMDK disk image:

```bash
cd ~/aruba-cx-agent-setup
mkdir -p ova-extracted
cd ova-extracted
tar xf "../Auba Simulator/ArubaOS-CX_10_07_0010.ova"
# Extracts:
#   arubaoscx-disk-image-genericx86-p4-20210610000730.ovf
#   arubaoscx-disk-image-genericx86-p4-20210610000730.vmdk

cd ~/aruba-cx-agent-setup
qemu-img convert -f vmdk -O qcow2 \
  ova-extracted/arubaoscx-disk-image-genericx86-p4-20210610000730.vmdk \
  aruba-cx.qcow2
```

**VM specs from OVF:** 2 vCPU, 4GB RAM, IDE disk, 10.4GB virtual disk, Linux 64-bit

**Status:** DONE
**Verification:** `qemu-img info aruba-cx.qcow2` shows 10.4 GiB virtual, 1.4 GiB disk

### Step 4: Boot Aruba CX VM with QEMU (with asciinema recording)

```bash
# Start tmux session with asciinema recording
tmux new-session -d -s aruba -x 120 -y 40 \
  'asciinema rec --overwrite ~/aruba-cx-agent-setup/session.cast -c \
  "qemu-system-x86_64 \
    -enable-kvm -m 4096 -smp 2 \
    -drive file=~/aruba-cx-agent-setup/aruba-cx.qcow2,if=ide,format=qcow2 \
    -netdev user,id=net0,hostfwd=tcp::2222-:22,hostfwd=tcp::8443-:443 \
    -device e1000,netdev=net0 \
    -nographic -serial mon:stdio"'

# Wait ~30 seconds for boot, then check output
sleep 30
tmux capture-pane -t aruba -p
```

**QEMU networking:** User-mode networking with port forwarding:
- Host port 2222 -> VM port 22 (SSH)
- Host port 8443 -> VM port 443 (HTTPS/REST API)
- VM gets DHCP address 10.0.2.15/24, gateway 10.0.2.2

**Status:** DONE
**Note:** The switch boots to a login prompt in about 30 seconds

### Step 5: Initial Switch Configuration

Default credentials: admin / (no password - first login forces password change)

```text
# Login
switch login: admin
Password: (empty - press Enter)
# Force password change
Enter new password: YourPassword123!
Confirm new password: YourPassword123!

# Enter config mode and enable services
switch# configure terminal
switch(config)# ssh server vrf default
switch(config)# https-server rest access-mode read-write
switch(config)# https-server vrf default
switch(config)# https-server vrf mgmt
switch(config)# exit

# Verify
switch# show system
# Hostname: switch
# System Description: Virtual.10.07.0010
# ArubaOS-CX Version: Virtual.10.07.0010

switch# show interface mgmt
# Address Mode: dhcp
# IPv4 address/subnet-mask: 10.0.2.15/24
# Default gateway IPv4: 10.0.2.2
```

**Status:** DONE

### Step 6: Access Linux Subsystem

```text
switch# start-shell
switch:~$ whoami
admin
switch:~$ uname -a
Linux switch 4.19.68-yocto-standard #1 SMP PREEMPT Mon Apr 19 23:30:49 UTC 2021 x86_64
switch:~$ cat /etc/os-release
ID=cnos
NAME=ArubaOS-CX
VERSION=Virtual.10.07.0010
```

Available tools: python3 (3.7.4), python, curl, wget
Pre-installed Python libraries: requests (2.22.0), json (stdlib)
No pip available (but not needed)

**Status:** DONE

### Step 7: Install AI Agent on Switch

Since the switch has Python 3.7.4 with `requests` pre-installed, we can write
a Python script directly. We used base64 encoding to transfer the script
through the tmux serial console (since SCP is not supported on the switch SSH).

```bash
# On the host: encode the agent script
base64 -w0 agent.py

# On the switch (via tmux): decode and save
echo '<base64_data>' | base64 -d > /tmp/agent.py
chmod +x /tmp/agent.py
```

**Agent script location:** /tmp/agent.py on the switch

**Status:** DONE
**See:** The agent.py file in ~/aruba-cx-agent-setup/ on the host

### Step 8: Configure Agent to Connect to Ollama

The agent is configured with:
- OLLAMA_URL = "http://YOUR_OLLAMA_SERVER:11434"
- API_KEY = "your-api-key" (any string works - Ollama doesn't validate by default)
- MODEL = "glm-5.2:cloud"
- Uses OpenAI-compatible /v1/chat/completions endpoint
- Includes system prompt identifying it as an Aruba CX switch agent

### Step 9: Verify Ollama Connectivity

```bash
# From the switch Linux shell:
curl -s http://YOUR_OLLAMA_SERVER:11434/api/tags | python3 -m json.tool
```

Available models on the Ollama server:
- gemma4:cloud
- kimi-k2.7-code:cloud
- kimi-k2.6:cloud
- glm-5.2:cloud
- deepseek-v4-pro:cloud
- nemotron-3-super:cloud
- qwen3-coder-next:cloud
- qwen3-vl:235b-cloud
- glm-4.7:cloud
- gpt-oss:120b-cloud
- kimi-k2.5:cloud
- minimax-m2.5:cloud
- qwen3.5:397b-cloud
- glm-5:cloud

**Status:** DONE

### Step 10: Test the AI Agent

```bash
# Single prompt mode:
python3 /tmp/agent.py "Hello, what model are you?"
# Response: "Hello! I am an AI assistant currently running on an Aruba CX network switch..."

python3 /tmp/agent.py "What is 2+2 and what switch am I running on?"
# Response: "2+2 equals 4. You are currently interacting with an Aruba CX network switch..."

python3 /tmp/agent.py "List 3 things you can help with on this Aruba switch"
# Response: Lists network config, troubleshooting, and system navigation

# Interactive mode:
python3 /tmp/agent.py
# Starts interactive chat session with prompt "You> "
```

**Status:** DONE - Agent is fully functional!

---

## Agent Script (agent.py)

```python
#!/usr/bin/env python3
"""Aruba CX Switch AI Agent - Connects to Ollama for LLM inference"""

import requests
import json
import sys

OLLAMA_URL = "http://YOUR_OLLAMA_SERVER:11434"
API_KEY = "your-api-key"
MODEL = "glm-5.2:cloud"

SYSTEM_PROMPT = """You are an AI assistant running on an Aruba CX network switch.
The switch runs ArubaOS-CX 10.07.0010 on a Yocto-based Linux environment.
You can help with network configuration, troubleshooting, and general questions.
You have access to the switch CLI and Linux shell."""

def chat(prompt):
    """Send a chat completion request to Ollama"""
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
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ]
            },
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Error: {e}"

def interactive():
    print("=" * 50)
    print("  Aruba CX Switch AI Agent")
    print(f"  Model: {MODEL}")
    print(f"  Ollama: {OLLAMA_URL}")
    print("  Type 'exit' to quit")
    print("=" * 50)
    while True:
        try:
            user_input = input("\nYou> ")
            if user_input.lower() in ("exit", "quit", "q"):
                print("Goodbye!")
                break
            print("\nAgent> ", end="", flush=True)
            response = chat(user_input)
            print(response)
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break

if __name__ == "__main__":
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
        print(chat(prompt))
    else:
        interactive()
```

---

## How to Recreate This Setup

1. Install packages:
   ```bash
   sudo apt install -y qemu-system-x86 qemu-utils asciinema tmux sshpass python3-pip
   pip3 install gdown --break-system-packages
   ```

2. Download the OVA:
   ```bash
   mkdir -p ~/aruba-cx-agent-setup && cd ~/aruba-cx-agent-setup
   gdown --folder "https://drive.google.com/drive/folders/1s26RdIueJPQpNeDnN3-JhDxUt2MFOL6V"
   ```

3. Extract and convert:
   ```bash
   mkdir -p ova-extracted && cd ova-extracted
   tar xf "../Auba Simulator/ArubaOS-CX_10_07_0010.ova"
   cd ..
   qemu-img convert -f vmdk -O qcow2 \
     ova-extracted/arubaoscx-disk-image-genericx86-p4-20210610000730.vmdk \
     aruba-cx.qcow2
   ```

4. Boot the VM (with recording):
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

5. Login and configure:
   ```bash
   # Send keys through tmux:
   tmux send-keys -t aruba 'admin' Enter
   sleep 3
   tmux send-keys -t aruba '' Enter  # empty password
   sleep 3
   tmux send-keys -t aruba 'YourPassword123!' Enter  # new password
   sleep 2
   tmux send-keys -t aruba 'YourPassword123!' Enter  # confirm
   sleep 3
   # Enable SSH and REST API
   tmux send-keys -t aruba 'configure terminal' Enter
   tmux send-keys -t aruba 'ssh server vrf default' Enter
   tmux send-keys -t aruba 'https-server rest access-mode read-write' Enter
   tmux send-keys -t aruba 'https-server vrf default' Enter
   tmux send-keys -t aruba 'exit' Enter
   ```

6. Access Linux shell and deploy agent:
   ```bash
   tmux send-keys -t aruba 'start-shell' Enter
   # Transfer agent.py via base64 through tmux
   B64=$(base64 -w0 agent.py)
   tmux send-keys -t aruba "echo '$B64' | base64 -d > /tmp/agent.py && chmod +x /tmp/agent.py" Enter
   ```

7. Test the agent:
   ```bash
   tmux send-keys -t aruba 'python3 /tmp/agent.py "Hello!"' Enter
   ```

---

## Files Created

- `~/aruba-cx-agent-setup/setup_notes.md` - This file
- `~/aruba-cx-agent-setup/agent.py` - The AI agent Python script
- `~/aruba-cx-agent-setup/start_agent.sh` - Shell launcher script
- `~/aruba-cx-agent-setup/aruba-cx.qcow2` - QEMU disk image (1.4GB)
- `~/aruba-cx-agent-setup/ova-extracted/` - Extracted OVA contents
- `~/aruba-cx-agent-setup/Auba Simulator/` - Downloaded OVA files
- `~/aruba-cx-agent-setup/session.cast` - asciinema terminal recording

On the switch (/tmp/):
- `/tmp/agent.py` - The AI agent script
- `/tmp/start_agent.sh` - The launcher script
- `/tmp/agent_b64.txt` - Temporary base64 file (can be deleted)

---

## Agent v3 - With Switch Control + Context Memory (LATEST)

### Key Discovery: vtysh Command Access
The Aruba CX switch CLI can be invoked from the Linux shell via:
```bash
vtysh -c "show interface"           # Single show command
vtysh -c "show vlan" -c "show system"  # Multiple commands
vtysh -c "configure terminal" -c "vlan 100" -c "name MGMT" -c "exit" -c "end"  # Config
```

### Agent v3 Features (improvements over v2):
- **Full context memory**: Conversation history maintained across all turns
  - You can ask follow-up questions that reference previous answers
  - "Yes, save it" works because agent remembers the previous exchange
  - "Type 'clear' to reset conversation history
- **Tool calling**: LLM autonomously runs switch commands
  - run_cli: Run any single CLI command
  - run_cli_batch: Run multiple commands in sequence (for configuration)
  - show_status: Get comprehensive switch overview
  - write_memory: Save config to flash
- **Better system prompt**: Includes command reference, examples, and notes
- **Up to 10 tool call rounds** per user request
- **Interactive and single-command modes**

### Deploying agent_v3.py to the Switch
The script is ~12KB, transferred via base64 encoding in 400-char chunks through tmux:
```bash
# On host: encode, chunk, and send via tmux
B64=$(base64 -w0 ~/aruba-cx-agent-setup/agent_v3.py)
# Send in chunks (see deployment script), then on switch:
base64 -d /tmp/agent_v3_b64.txt > /tmp/agent_v3.py && chmod +x /tmp/agent_v3.py
```

### Using agent_v3.py
```bash
# From the switch Linux shell (start-shell):
python3 /tmp/agent_v3.py

# Or single command:
python3 /tmp/agent_v3.py "Show me the status of all ports"
python3 /tmp/agent_v3.py "Create VLAN 100 named MGMT and enable port 1/1/1"

# Interactive mode (with context):
python3 /tmp/agent_v3.py
# Then:
# > Tell me which interfaces have a description
# > Now set a description on port 1/1/1 that says "UPLINK-CORE-01"
# > Yes, save it. Also tell me what other ports need descriptions
# > Set descriptions on all remaining ports
# > Now show me which interfaces have descriptions and confirm
# All of these maintain context - the agent remembers previous answers!
```

### Tested Scenarios (all working):
1. "Tell me which interfaces have a description set" -> Correctly found port 1/1/9 with "Test"
2. "Now set a description on port 1/1/1 that says UPLINK-CORE-01" -> Done, verified
3. "Yes, save it. Also tell me what other ports still need descriptions besides
   the two we already set" -> Context maintained! Saved config, listed remaining 50 ports
4. "Set descriptions on all the remaining ports. Name them PORT-01, PORT-02, etc"
   -> Configured all 50 ports, saved config, kept context of which were already named
5. "Now show me which interfaces have descriptions and confirm they all have one"
   -> Verified all 52 ports have descriptions

### How Context Memory Works
The agent maintains a `conversation` list that persists across all user turns.
Each user message and assistant response (including tool call results) is appended.
The full conversation is sent to the LLM on each request, so it can reference
previous questions, answers, and switch state changes.

---

## Notes and Caveats

1. The simulator OVA provides user-level Linux access (admin, not root)
2. Python 3.7.4 with `requests` is pre-installed - no pip needed
3. SCP is not supported by the switch SSH server - use base64 transfer or SSH pipe
4. The switch SSH server works for interactive sessions (port 2222 on host)
5. REST API returned 401 on the 10.07 simulator - may need additional configuration
6. QEMU user-mode networking allows outbound HTTP to the Ollama server
7. The asciinema recording captures all terminal I/O for playback/review
8. The agent uses the OpenAI-compatible API endpoint (/v1/chat/completions)
9. Ollama doesn't validate API keys by default, so any string works
10. The model name is `glm-5.2:cloud` (not glm-4.5:cloud as initially expected)

---

## Progress Log

* 2026-07-31 14:40 - Started project, researching OVA sources
* 2026-07-31 14:42 - Downloaded OVA from Google Drive (520MB + 532MB)
* 2026-07-31 14:43 - Extracted OVA and converted to qcow2
* 2026-07-31 14:43 - Started QEMU VM with asciinema recording
* 2026-07-31 14:44 - VM booted, logged in, set admin password
* 2026-07-31 14:45 - Configured SSH and REST API
* 2026-07-31 14:46 - Accessed Linux shell via start-shell
* 2026-07-31 14:47 - Discovered Python 3.7.4 with requests pre-installed
* 2026-07-31 14:48 - Verified Ollama connectivity (YOUR_OLLAMA_SERVER:11434)
* 2026-07-31 14:49 - Created and deployed agent.py on the switch
* 2026-07-31 14:50 - Successfully tested AI agent - got LLM responses
* 2026-07-31 14:51 - Multiple successful test queries confirmed working