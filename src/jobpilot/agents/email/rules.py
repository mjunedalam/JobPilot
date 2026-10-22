"""Deterministic email rules: the cheap job-mail prefilter, a keyword fallback classifier (used
when no model is reachable) and OTP extraction (never done by a model)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse

import tldextract

from jobpilot.mail.base import MailMessage, domain_of


class Category(StrEnum):
    INTERVIEW = "interview_invite"
    RECRUITER = "recruiter_outreach"
    ASSESSMENT = "assessment"
    REJECTION = "rejection"
    OFFER = "offer"
    DOCUMENTS = "document_request"
    CONFIRMATION = "application_confirmation"
    OTHER = "other"


# Spec: notify for any email needing action. Decided here, not by the model.
NEEDS_ACTION: dict[Category, bool] = {
    Category.INTERVIEW: True,
    Category.RECRUITER: True,
    Category.ASSESSMENT: True,
    Category.OFFER: True,
    Category.DOCUMENTS: True,
    Category.REJECTION: False,
    Category.CONFIRMATION: False,
    Category.OTHER: False,
}

SUGGESTED_ACTION: dict[Category, str] = {
    Category.INTERVIEW: "Reply with your availability",
    Category.RECRUITER: "Reply if the role interests you",
    Category.ASSESSMENT: "Complete the assessment before the deadline",
    Category.OFFER: "Review the offer and reply",
    Category.DOCUMENTS: "Send the requested documents",
    Category.REJECTION: "No action needed",
    Category.CONFIRMATION: "No action needed",
    Category.OTHER: "No action needed",
}

# Job portals and applicant-tracking systems that send candidate mail.
JOB_SENDER_DOMAINS = frozenset(
    {
        "naukri.com", "linkedin.com", "shine.com", "indeed.com", "foundit.in", "monster.com",
        "instahyre.com", "iimjobs.com", "hirist.tech", "glassdoor.com", "wellfound.com",
        "cutshort.io", "greenhouse.io", "lever.co", "myworkday.com", "myworkdayjobs.com",
        "smartrecruiters.com", "ashbyhq.com", "icims.com", "taleo.net", "successfactors.com",
        "zohorecruit.com", "keka.com", "darwinbox.in", "hackerrank.com", "hackerearth.com",
        "codility.com", "mettl.com", "testgorilla.com",
    }
)  # fmt: skip

# Free mail and shared hosting/form platforms: anyone can have an address or page there, so
# they never identify an employer. (PSL private suffixes such as github.io are handled by the
# suffix list itself.)
SHARED_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "google.com", "outlook.com", "hotmail.com", "live.com",
        "msn.com", "microsoft.com", "office.com", "yahoo.com", "yahoo.co.in", "icloud.com",
        "me.com", "proton.me", "protonmail.com", "zoho.com", "zohomail.in", "yandex.com",
        "aol.com", "gmx.com", "mail.com", "rediffmail.com", "notion.so", "notion.site",
        "typeform.com", "forms.gle", "bit.ly", "linktr.ee", "medium.com", "wordpress.com",
        "blogspot.com", "wixsite.com", "squarespace.com", "github.com", "gitlab.com",
        "amazonaws.com", "cloudfront.net", "azurewebsites.net", "herokuapp.com",
        "google.co.in", "calendly.com", "zoom.us",
    }
)  # fmt: skip

# Public Suffix List, bundled snapshot only (never fetched at run time).
_PSL = tldextract.TLDExtract(suffix_list_urls=(), include_psl_private_domains=True, cache_dir=None)

_JOB_WORDS = re.compile(
    r"\b(interview|application|applied|applying|candidate|candidature|position|role|opening|"
    r"opportunit(?:y|ies)|recruit(?:er|ment|ing)|hiring|talent acquisition|offer letter|"
    r"assessment|shortlist(?:ed)?|resume|cv|job|career|ctc|notice period|hr team)\b",
    re.I,
)

_RULES: list[tuple[Category, re.Pattern[str]]] = [
    (
        Category.OFFER,
        re.compile(r"offer letter|pleased to (?:extend|offer)|(?:job|employment) offer", re.I),
    ),
    (
        Category.REJECTION,
        re.compile(
            r"unfortunately|regret to inform|not (?:be )?moving forward|decided to (?:proceed|move "
            r"forward) with other|not (?:been )?selected|position has been filled",
            re.I,
        ),
    ),
    (
        Category.ASSESSMENT,
        re.compile(
            r"assessment|coding (?:test|challenge|round)|online test|test link|hackerrank|"
            r"hackerearth|codility|take-home",
            re.I,
        ),
    ),
    (
        Category.INTERVIEW,
        re.compile(
            r"\binterview\b|schedule (?:a|the) (?:call|discussion|meeting)|your availability|"
            r"(?:technical|hr|first|next) round",
            re.I,
        ),
    ),
    (
        Category.DOCUMENTS,
        re.compile(
            r"(?:share|send|submit|upload) (?:your |the )?(?:updated )?(?:documents|payslips?|"
            r"pan|aadhaar|relieving letter|certificates|id proof)",
            re.I,
        ),
    ),
    (
        Category.CONFIRMATION,
        re.compile(
            r"application (?:has been |was )?(?:received|submitted)|thank you for (?:applying|your "
            r"application)|we have received your application",
            re.I,
        ),
    ),
    (
        Category.RECRUITER,
        re.compile(
            r"came across your profile|your profile (?:matches|looks)|are you open to|"
            r"exciting opportunity|would you be interested",
            re.I,
        ),
    ),
]

_OTP_WORDS = re.compile(
    r"\b(otp|one[- ]time (?:password|code)|verification code|login code)\b", re.I
)
_OTP_CODE = re.compile(
    r"(?:otp|code|password|pin)\b[^0-9\n]{0,40}?\b(\d{4,8})\b|\b(\d{4,8})\b[^0-9\n]{0,30}?"
    r"\b(?:is your|as your)\b",
    re.I,
)


def registrable_domain(host: str) -> str:
    """careers.acme.co.in → acme.co.in; acme.github.io stays acme.github.io (Public Suffix List)."""
    parts = _PSL(host.lower().strip("."))
    return parts.top_domain_under_public_suffix or ""


def host_of(url_or_host: str) -> str:
    parsed = urlparse(url_or_host if "//" in url_or_host else f"//{url_or_host}")
    return parsed.hostname or ""


def is_job_sender(message: MailMessage) -> bool:
    domain = domain_of(message.sender)
    return bool(domain) and registrable_domain(domain) in JOB_SENDER_DOMAINS


def normalize_company(name: str) -> str:
    n = re.sub(
        r"\b(private|pvt|limited|ltd|llp|llc|inc|corp(?:oration)?|technologies|technology|"
        r"solutions|services|software|labs|india|global|group|co)\b\.?",
        " ",
        name.casefold(),
    )
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", n).split())


def mentions_company(message: MailMessage, company: str) -> bool:
    key = normalize_company(company)
    if len(key) < 3:
        return False
    haystack = f"{message.sender} {message.subject} {message.body[:3000]}".casefold()
    if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", haystack):
        return True
    domain = domain_of(message.sender)
    label = registrable_domain(domain).split(".")[0].replace("-", "") if domain else ""
    return bool(label) and label == key.replace(" ", "")


def company_domains(source_urls: list[str]) -> set[str]:
    """Domains known to belong to the company: the registrable domains of the job's own
    listings, minus job portals and ATSs (shared by every employer). Never guessed from the
    company's name."""
    hosts = {registrable_domain(host_of(u)) for u in source_urls if host_of(u)}
    return {h for h in hosts if h and h not in JOB_SENDER_DOMAINS | SHARED_DOMAINS}


@dataclass(frozen=True)
class Signal:
    reason: str  # thread | job_sender | company | keywords
    strong: bool  # strong signals are stored even if the model calls the mail non-job


def job_signal(
    message: MailMessage, *, linked_threads: set[str], companies: list[str]
) -> Signal | None:
    if message.thread_id in linked_threads:
        return Signal("thread", True)
    if is_job_sender(message):
        return Signal("job_sender", False)
    if any(mentions_company(message, c) for c in companies):
        return Signal("company", True)  # a company you applied to
    text = f"{message.subject}\n{message.body[:3000]}"
    hits = {m.group(0).casefold() for m in _JOB_WORDS.finditer(text)}
    if len(hits) >= 2:
        return Signal("keywords", False)
    return None


def rule_category(message: MailMessage) -> Category:
    text = f"{message.subject}\n{message.body[:5000]}"
    for category, pattern in _RULES:
        if pattern.search(text):
            return category
    return Category.OTHER


def is_otp_message(message: MailMessage) -> bool:
    return bool(_OTP_WORDS.search(f"{message.subject}\n{message.body[:2000]}"))


def extract_otp(message: MailMessage) -> str | None:
    if not is_otp_message(message):
        return None
    m = _OTP_CODE.search(f"{message.subject}\n{message.body[:4000]}")
    if m is None:
        return None
    return m.group(1) or m.group(2)
