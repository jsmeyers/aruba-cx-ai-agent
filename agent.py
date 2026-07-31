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