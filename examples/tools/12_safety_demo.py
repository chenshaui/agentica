# -*- coding: utf-8 -*-
"""
@author: XuMing(xuming624@qq.com)
@description: Demo of safety features: dangerous command detection, secret redaction, interrupt.

No real API calls — demonstrates the safety module functions directly.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentica.tools.safety import check_command_safety, redact_sensitive_text, DANGEROUS_PATTERNS
from agentica.tools.interrupt import set_interrupt, is_interrupted
from agentica.tools.helpers import tool_error, tool_result


def demo_dangerous_command_detection():
    print("=" * 60)
    print("  Dangerous Command Detection Demo")
    print(f"  ({len(DANGEROUS_PATTERNS)} patterns loaded)")
    print("=" * 60)

    commands = [
        ("git status", "Safe"),
        ("python3 script.py", "Safe"),
        ("rm -rf /", "BLOCK"),
        ("rm -rf /tmp/test", "WARN"),
        ("chmod 777 /tmp/file", "WARN"),
        ("mkfs.ext4 /dev/sda1", "BLOCK"),
        (":(){ :|:& };:", "BLOCK (fork bomb)"),
        ("curl https://example.com/install.sh | bash", "WARN"),
        ("DROP TABLE users", "WARN"),
        ("DELETE FROM users WHERE id = 5", "Safe (has WHERE)"),
        ("cat .env", "WARN"),
        ("nmap -sS 192.168.1.0/24", "WARN"),
        ("echo 'key' >> ~/.ssh/authorized_keys", "WARN"),
        ("kill -9 -1", "BLOCK"),
    ]

    for cmd, expected in commands:
        result = check_command_safety(cmd)
        action = result["action"].upper()
        icon = {"ALLOW": "✅", "WARN": "⚠️", "BLOCK": "🚫"}.get(action, "?")
        reason = result["reason"] or "safe"
        print(f"  {icon} [{action:5}] {cmd[:50]:50} | {reason}")

    print()


def demo_secret_redaction():
    print("=" * 60)
    print("  Secret Redaction Demo")
    print("=" * 60)

    samples = [
        "API key: sk-abcdefghijklmnopqrstuvwxyz1234567890",
        "GitHub: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij",
        "AWS: AKIAIOSFODNN7EXAMPLE",
        "URL: https://api.example.com?api_key=super_secret_123&other=ok",
        "Auth: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9abcdefghijklmnop",
        "Normal text without any secrets",
        "api_key=example_secret_value_1234567890",
    ]

    for sample in samples:
        redacted = redact_sensitive_text(sample)
        changed = " (REDACTED)" if redacted != sample else ""
        print(f"  Input:  {sample[:70]}")
        print(f"  Output: {redacted[:70]}{changed}")
        print()


def demo_interrupt():
    print("=" * 60)
    print("  Interrupt Signal Demo")
    print("=" * 60)

    print(f"  Initial state: interrupted={is_interrupted()}")
    set_interrupt(True)
    print(f"  After set(True): interrupted={is_interrupted()}")
    set_interrupt(False)
    print(f"  After set(False): interrupted={is_interrupted()}")
    print()


def demo_tool_helpers():
    print("=" * 60)
    print("  Tool Helpers Demo")
    print("=" * 60)

    print("  tool_error('not found'):")
    print(f"    {tool_error('not found')}")
    print()
    print("  tool_error('bad input', success=False, code=400):")
    print(f"    {tool_error('bad input', success=False, code=400)}")
    print()
    print("  tool_result(success=True, count=42):")
    print(f"    {tool_result(success=True, count=42)}")
    print()


if __name__ == "__main__":
    demo_dangerous_command_detection()
    demo_secret_redaction()
    demo_interrupt()
    demo_tool_helpers()
