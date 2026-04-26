# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Sensitive text redaction shared by logs, tools, archives, and compression.
"""

import re
from typing import Optional


_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----"
)

_DB_CONNSTR_RE = re.compile(
    r"((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:\s/@]+:)([^@\s]+)(@)",
    re.IGNORECASE,
)

_AUTH_HEADER_RE = re.compile(r"(Authorization:\s*Bearer\s+)(\S+)", re.IGNORECASE)

_BARE_BEARER_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(Bearer\s+)([A-Za-z0-9._~+/=-]{20,})(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)

_URL_QUERY_SECRET_RE = re.compile(
    r"([?&](?:access_token|refresh_token|id_token|api[_-]?key|apikey|auth[_-]?token|token|secret|password|signature|sig|client_secret|jwt|key|code)=)"
    r"([^&#\s]+)",
    re.IGNORECASE,
)

_ENV_ASSIGN_RE = re.compile(
    r"(?<![?&A-Za-z0-9_])([A-Z0-9_]*(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)[A-Z0-9_]*)\s*=\s*(['\"]?)(\S+)\2",
    re.IGNORECASE,
)

_JSON_SECRET_FIELD_RE = re.compile(
    r'("(?:api_?key|token|secret|password|access_token|refresh_token|auth_token|bearer|private_key)")\s*:\s*"([^"]+)"',
    re.IGNORECASE,
)

_KEY_VALUE_SECRET_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"([A-Z0-9_-]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)[A-Z0-9_-]*\s*:\s*)"
    r"(['\"]?)([^\s,'\"}]+)\2",
    re.IGNORECASE,
)

_PREFIX_RE = re.compile(
    r"(?<![A-Za-z0-9_-])("
    r"sk-proj-[A-Za-z0-9_-]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,}|"
    r"ghp_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"gho_[A-Za-z0-9]{20,}|"
    r"ghu_[A-Za-z0-9]{20,}|"
    r"ghs_[A-Za-z0-9]{20,}|"
    r"ghr_[A-Za-z0-9]{20,}|"
    r"AKIA[A-Z0-9]{16}|"
    r"AIza[A-Za-z0-9_-]{30,}|"
    r"hf_[A-Za-z0-9]{20,}|"
    r"gsk_[A-Za-z0-9]{20,}|"
    r"pypi-[A-Za-z0-9_-]{20,}"
    r")(?![A-Za-z0-9_-])"
)

_JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_=-]{4,}){0,2}(?![A-Za-z0-9_-])"
)


def redact_sensitive_text(text: Optional[str]) -> Optional[str]:
    """Mask common secret shapes in text.

    This is a logging and persistence safety net, not a permission boundary.
    Callers should still avoid sending secrets into prompts or archives.
    """
    if not text:
        return text

    redacted = _PRIVATE_KEY_RE.sub("***REDACTED_PRIVATE_KEY***", text)
    redacted = _DB_CONNSTR_RE.sub(r"\1***REDACTED***\3", redacted)
    redacted = _AUTH_HEADER_RE.sub(r"\1***REDACTED***", redacted)
    redacted = _BARE_BEARER_RE.sub(r"\1***REDACTED***", redacted)
    redacted = _URL_QUERY_SECRET_RE.sub(r"\1***", redacted)
    redacted = _ENV_ASSIGN_RE.sub(r"\1=***REDACTED***", redacted)
    redacted = _JSON_SECRET_FIELD_RE.sub(r'\1: "***REDACTED***"', redacted)
    redacted = _KEY_VALUE_SECRET_RE.sub(r"\1\2***REDACTED***\2", redacted)
    redacted = _PREFIX_RE.sub("***REDACTED_SECRET***", redacted)
    redacted = _JWT_RE.sub("***REDACTED_JWT***", redacted)
    return redacted
