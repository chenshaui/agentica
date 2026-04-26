# -*- coding: utf-8 -*-
"""
@author: XuMing(xuming624@qq.com)
@description: Command safety detection and secret redaction.

Dangerous command patterns (31 regex) detect risky shell operations.
Secret redaction removes API keys, tokens, passwords from tool output.
"""
import logging
import re
from typing import List, Tuple

from agentica.security.redact import redact_sensitive_text

logger = logging.getLogger(__name__)

# ============== Dangerous Command Patterns ==============
# Adapted from hermes-agent tools/approval.py
# Each tuple: (regex_pattern, human_description)

DANGEROUS_PATTERNS: List[Tuple[str, str]] = [
    # Destructive file operations
    (r"\brm\s+(-[^\s]*\s+)*/\s*$", "delete root filesystem"),
    (r"\brm\s+(-[^\s]*\s+)*/\*", "delete all files in root"),
    (r"\brm\s+-[^\s]*r", "recursive delete"),
    (r"\brm\s+--recursive\b", "recursive delete (long flag)"),
    (r"\brm\s+-[^\s]*f[^\s]*\s+~", "force delete in home directory"),
    # Permissions
    (r"\bchmod\s+(-[^\s]*\s+)*(777|666|o\+[rwx]*w|a\+[rwx]*w)\b", "world-writable permissions"),
    (r"\bchmod\s+--recursive\b.*(777|666)", "recursive world-writable"),
    (r"\bchown\s+(-[^\s]*)?R\s+root", "recursive chown to root"),
    # Filesystem
    (r"\bmkfs\b", "format filesystem"),
    (r"\bdd\s+.*if=", "disk copy"),
    (r">\s*/dev/sd", "write to block device"),
    # SQL
    (r"\bDROP\s+(TABLE|DATABASE)\b", "SQL DROP"),
    (r"\bDELETE\s+FROM\b(?!.*\bWHERE\b)", "SQL DELETE without WHERE"),
    (r"\bTRUNCATE\s+TABLE\b", "SQL TRUNCATE"),
    # Remote code execution
    (r"curl\s+[^\n]*\|\s*(bash|sh|python|ruby|perl)", "pipe remote code to shell"),
    (r"wget\s+[^\n]*\|\s*(bash|sh|python|ruby|perl)", "pipe remote code to shell"),
    (r"curl\s+[^\n]*-o\s+/tmp/[^\s]*\s*&&\s*(bash|sh|chmod)", "download and execute"),
    # Process manipulation
    (r"\bkill\s+-9\s+-1\b", "kill all processes"),
    (r"\bkillall\s+-9\b", "kill all by name"),
    # Fork bomb
    (r":\(\)\s*\{\s*:\|:&\s*\}\s*;:", "fork bomb"),
    # System modification
    (r"/etc/sudoers\b", "sudoers modification"),
    (r"\bvisudo\b", "sudoers edit"),
    (r"\bpasswd\s+root\b", "change root password"),
    # SSH
    (r"authorized_keys", "SSH authorized_keys modification"),
    (r"\bssh-keygen\b.*-f\s+/", "SSH key generation in system path"),
    # Persistence
    (r"\bcrontab\s+-r\b", "clear all cron jobs"),
    (r"\.bashrc|\.zshrc|\.profile", "shell config modification"),
    # Exfiltration
    (r"curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", "potential credential exfiltration via curl"),
    (r"wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", "potential credential exfiltration via wget"),
    # Sensitive file access
    (r"\bcat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass)", "read sensitive files"),
    # Network scanning
    (r"\bnmap\b", "network scanning"),
    # Container escape
    (r"\bnsenter\b", "namespace enter (container escape)"),
]

_COMPILED_PATTERNS = [(re.compile(p, re.IGNORECASE), desc) for p, desc in DANGEROUS_PATTERNS]


def check_command_safety(command: str) -> dict:
    """Check a shell command for dangerous patterns.

    Args:
        command: The shell command to check.

    Returns:
        {
            "action": "allow" | "warn" | "block",
            "reason": str,  # Empty if allowed
            "pattern": str,  # Pattern name if matched
        }
    """
    for compiled, description in _COMPILED_PATTERNS:
        if compiled.search(command):
            # Destructive operations that should be blocked
            if description in (
                "delete root filesystem", "delete all files in root",
                "format filesystem", "fork bomb",
                "kill all processes", "disk copy", "write to block device",
            ):
                return {
                    "action": "block",
                    "reason": f"Blocked: {description}",
                    "pattern": description,
                }
            # Everything else is a warning
            return {
                "action": "warn",
                "reason": f"Warning: {description}",
                "pattern": description,
            }
    return {"action": "allow", "reason": "", "pattern": ""}
