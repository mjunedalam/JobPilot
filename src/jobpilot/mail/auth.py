"""Is this email really from the domain in its From: header? (RFC 8601 Authentication-Results)

Only the topmost Authentication-Results header is used: the receiving provider adds it on
top, and anything below may come from the sender. It must also name the provider that wrote it
(the authserv-id), and its DMARC result must be `pass` for exactly the From: domain. Comments
and quoted strings are removed before parsing, so text like `(dmarc=pass)` or
`reason="dmarc=pass"` can't pass for a result."""

from __future__ import annotations

from dataclasses import dataclass

# authserv-id each provider writes. Microsoft 365 omits it (its first clause is a result),
# so for Outlook the topmost header is accepted as Microsoft's own stamp.
AUTHSERV_IDS: dict[str, frozenset[str]] = {"gmail": frozenset({"mx.google.com"})}


@dataclass(frozen=True)
class AuthResult:
    method: str
    result: str
    props: dict[str, str]


def _strip_comments_and_quotes(value: str) -> str:
    out: list[str] = []
    depth, quoted, escaped = 0, False, False
    for ch in value:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if quoted:
            if ch == '"':
                quoted = False
                out.append('""')  # keep a placeholder token, drop the content
            continue
        if ch == '"' and depth == 0:
            quoted = True
            continue
        if ch == "(":
            depth += 1
            continue
        if ch == ")" and depth:
            depth -= 1
            continue
        if depth == 0:
            out.append(ch)
    return "".join(out)


def parse(header: str) -> tuple[str, list[AuthResult]]:
    """(authserv-id or "", results). The authserv-id is "" when the first clause is a result."""
    clauses = [c.strip() for c in _strip_comments_and_quotes(header).split(";")]
    clauses = [c for c in clauses if c]
    if not clauses:
        return "", []
    authserv = ""
    first = clauses[0].split()
    if first and "=" not in first[0]:
        authserv = first[0].lower()
        clauses = clauses[1:]
    results: list[AuthResult] = []
    for clause in clauses:
        tokens = clause.split()
        if not tokens or "=" not in tokens[0]:
            continue
        method, _, result = tokens[0].partition("=")
        props = {}
        for token in tokens[1:]:
            key, sep, val = token.partition("=")
            if sep:
                props[key.lower()] = val.lower().strip(".")
        results.append(AuthResult(method.lower(), result.lower(), props))
    return authserv, results


def dmarc_pass(header: str, *, provider: str, from_domain: str) -> bool:
    if not header or not from_domain:
        return False
    authserv, results = parse(header)
    expected = AUTHSERV_IDS.get(provider)
    if expected is not None and authserv not in expected:
        return False
    if provider == "outlook" and authserv:
        return False  # Microsoft's stamp has no authserv-id; one here was written by someone else
    if provider not in ("gmail", "outlook"):
        return False
    domain = from_domain.lower().strip(".")
    return any(
        r.method == "dmarc" and r.result == "pass" and r.props.get("header.from") == domain
        for r in results
    )
