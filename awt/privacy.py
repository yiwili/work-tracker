import re

PATTERNS = [
    r'''(?i)["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|authorization|token)["']?\s*[:=]\s*(?:"[^"\r\n]*"|'[^'\r\n]*')''',
    r"\b[A-Z_][A-Z0-9_]{2,}\s*=\s*(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;]+)",
    r"(?i)\b(?:sk|ghp|github_pat|AKIA)[-_a-zA-Z0-9]{12,}\b",
    r"(?i)\bBearer\s+[\w.\-+/=]+",
    r"(?i)(?:[\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|authorization|token)[\"']?\s*[:=]\s*)[^\s,;\r\n]+",
    r"(?i)\b[A-Z][A-Z0-9_]*(?:KEY|TOKEN|PASSWORD|SECRET)[A-Z0-9_]*\s*=\s*[^\s,;]+",
    r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----",
    r"(?i)(?:https?|ssh|mysql|postgresql)://[^\s/@:]+:[^\s/@]+@",
]


def redact(text, custom=()):
    for pattern in [*PATTERNS, *custom]:
        text = re.sub(pattern, "[REDACTED]", str(text))
    return text
