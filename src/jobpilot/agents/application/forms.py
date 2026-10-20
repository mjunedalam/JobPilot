"""Read application forms from page HTML: fields with their labels, and the action buttons.

Pure functions over HTML so they are testable without a browser; the selectors they return are
what the browser later fills and clicks."""

from __future__ import annotations

import re
from enum import StrEnum

from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, ConfigDict


class FieldKind(StrEnum):
    TEXT = "text"
    TEXTAREA = "textarea"
    EMAIL = "email"
    TEL = "tel"
    NUMBER = "number"
    URL = "url"
    SELECT = "select"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    FILE = "file"


class FormField(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str  # stable identity across attempts (name or id)
    selector: str
    label: str
    kind: FieldKind
    required: bool = False
    options: tuple[str, ...] = ()  # select/radio choices (visible text)
    value: str = ""  # already filled (prefilled by the portal from the user's account)


class ButtonRole(StrEnum):
    APPLY = "apply"
    NEXT = "next"
    SUBMIT = "submit"


class Button(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: ButtonRole
    selector: str
    text: str


_SKIP_TYPES = {"hidden", "submit", "button", "reset", "image", "search", "password"}
_SPACES = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _SPACES.sub(" ", text).strip(" *: ")


def _selector(tag: Tag, index: int) -> str:
    if tag.get("id"):
        return f"#{tag['id']}"
    if tag.get("name"):
        return f'{tag.name}[name="{tag["name"]}"]'
    return f"{tag.name} >> nth={index}"


def _label_for(soup: BeautifulSoup, tag: Tag) -> str:
    if tag.get("id"):
        label = soup.find("label", attrs={"for": tag["id"]})
        if label is not None:
            return _clean(label.get_text(" "))
    parent = tag.find_parent("label")
    if parent is not None:
        return _clean(parent.get_text(" "))
    for attr in ("aria-label", "placeholder", "title"):
        if tag.get(attr):
            return _clean(str(tag[attr]))
    labelled = tag.get("aria-labelledby")
    if labelled:
        node = soup.find(id=str(labelled).split()[0])
        if node is not None:
            return _clean(node.get_text(" "))
    return _clean(str(tag.get("name", "")).replace("_", " ").replace("-", " "))


def _is_required(tag: Tag, label: str) -> bool:
    return tag.has_attr("required") or tag.get("aria-required") == "true" or label.endswith("*")


def extract_fields(html: str) -> list[FormField]:
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form") or soup
    fields: list[FormField] = []
    seen_radio: set[str] = set()
    for index, tag in enumerate(form.find_all(["input", "select", "textarea"])):
        kind_attr = str(tag.get("type", "text")).casefold() if tag.name == "input" else tag.name
        if tag.name == "input" and kind_attr in _SKIP_TYPES:
            continue
        if tag.has_attr("disabled"):
            continue
        parent_label = tag.find_parent("label")
        raw_label = parent_label.get_text(" ") if parent_label is not None else ""
        label = _label_for(soup, tag)
        name = str(tag.get("name") or tag.get("id") or f"field-{index}")
        if tag.name == "select":
            options = tuple(
                _clean(o.get_text(" "))
                for o in tag.find_all("option")
                if _clean(o.get_text(" ")) and o.get("value", "x") != ""
            )
            chosen = tag.find("option", selected=True)
            fields.append(
                FormField(
                    key=name,
                    selector=_selector(tag, index),
                    label=label,
                    kind=FieldKind.SELECT,
                    required=_is_required(tag, raw_label or label),
                    options=options,
                    value=_clean(chosen.get_text(" ")) if chosen and chosen.get("value") else "",
                )
            )
        elif kind_attr == "radio":
            if name in seen_radio:
                continue
            seen_radio.add(name)
            group = form.find_all("input", attrs={"type": "radio", "name": name})
            options = tuple(_label_for(soup, r) for r in group)
            legend = tag.find_parent("fieldset")
            group_label = _clean(legend.legend.get_text(" ")) if legend and legend.legend else label
            fields.append(
                FormField(
                    key=name,
                    selector=f'input[type="radio"][name="{name}"]',
                    label=group_label,
                    kind=FieldKind.RADIO,
                    required=any(r.has_attr("required") for r in group),
                    options=options,
                )
            )
        else:
            kind = {
                "textarea": FieldKind.TEXTAREA,
                "email": FieldKind.EMAIL,
                "tel": FieldKind.TEL,
                "number": FieldKind.NUMBER,
                "url": FieldKind.URL,
                "checkbox": FieldKind.CHECKBOX,
                "file": FieldKind.FILE,
            }.get(kind_attr, FieldKind.TEXT)
            value = tag.get_text() if tag.name == "textarea" else str(tag.get("value", ""))
            if kind is FieldKind.CHECKBOX:
                value = "true" if tag.has_attr("checked") else ""
            fields.append(
                FormField(
                    key=name,
                    selector=_selector(tag, index),
                    label=label,
                    kind=kind,
                    required=_is_required(tag, raw_label or label),
                    value=_clean(value),
                )
            )
    return fields


_APPLY = re.compile(
    r"^(easy apply|apply( now| for this job| on company site)?|i'?m interested)$", re.I
)
_SUBMIT = re.compile(r"^(submit( application)?|send( application)?|apply|finish|complete)$", re.I)
_NEXT = re.compile(r"^(next|continue|review( application)?|save (and|&) continue|proceed)$", re.I)


def find_buttons(html: str, *, in_form: bool) -> list[Button]:
    """In a form, classify Next/Submit; outside one, find the Apply entry point."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[Button] = []
    candidates = soup.select("button, input[type=submit], input[type=button], a[role=button], a")
    for tag in candidates:
        text = _clean(str(tag.get("value", "")) if tag.name == "input" else tag.get_text(" "))
        text = text or _clean(str(tag.get("aria-label", "")))
        if not text or len(text) > 40 or tag.has_attr("disabled"):
            continue
        if tag.get("id"):
            selector = f"#{tag['id']}"
        else:
            selector = (
                f'{tag.name}:text-is("{text}")' if tag.name != "input" else f'input[value="{text}"]'
            )
        if in_form and _NEXT.match(text):
            out.append(Button(role=ButtonRole.NEXT, selector=selector, text=text))
        elif in_form and _SUBMIT.match(text):
            out.append(Button(role=ButtonRole.SUBMIT, selector=selector, text=text))
        elif not in_form and _APPLY.match(text):
            out.append(Button(role=ButtonRole.APPLY, selector=selector, text=text))
    return out


_CONFIRMATION = re.compile(
    r"(application (has been |was )?(submitted|received|sent)"
    r"|thank(s| you) for (applying|your application)"
    r"|you('ve| have) (successfully )?applied|applied successfully)",
    re.I,
)


def confirmation_text(html: str) -> str | None:
    text = _clean(BeautifulSoup(html, "html.parser").get_text(" "))
    m = _CONFIRMATION.search(text)
    if not m:
        return None
    start = max(0, m.start() - 80)
    return text[start : m.end() + 120]
