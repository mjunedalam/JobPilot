from __future__ import annotations

from pathlib import Path

import pytest

from jobpilot.agents.application.answers import FieldAnswerer, choose_option
from jobpilot.agents.application.forms import (
    ButtonRole,
    FieldKind,
    confirmation_text,
    extract_fields,
    find_buttons,
)
from jobpilot.agents.application.selection import SelectionError, parse_selection
from jobpilot.agents.profile.repository import AnswerBank
from jobpilot.agents.profile.schema import CandidateProfile, Links, Money, Skill

FIX = Path(__file__).parents[1] / "fixtures" / "apply"
PROFILE = CandidateProfile(
    full_name="Asha Rao",
    email="asha.rao@example.com",
    phone="+91 98765 43210",
    total_experience_years=7,
    notice_period_days=60,
    expected_ctc=Money(amount=3_000_000, currency="INR"),
    skills=[Skill(name="Kafka", years=4)],
    links=Links(github="github.com/asharao"),
)


class TestSelection:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("all", [1, 2, 3, 4, 5, 6, 7, 8]),
            ("1,3,5-8", [1, 3, 5, 6, 7, 8]),
            ("2 4", [2, 4]),
            ("3,3,1", [3, 1]),
            ("none", []),
        ],
    )
    def test_valid(self, text: str, expected: list[int]) -> None:
        assert parse_selection(text, 8) == expected

    @pytest.mark.parametrize("text", ["9", "0", "5-3", "two", "1,x"])
    def test_invalid(self, text: str) -> None:
        with pytest.raises(SelectionError):
            parse_selection(text, 8)


class TestForms:
    def test_step1_fields(self) -> None:
        fields = {f.key: f for f in extract_fields((FIX / "step1.html").read_text())}
        assert set(fields) == {"fullName", "email", "phone", "cv", "exp"}  # hidden csrf skipped
        assert (
            fields["email"].value == "asha.rao@example.com"
            and fields["email"].kind is FieldKind.EMAIL
        )
        assert fields["cv"].kind is FieldKind.FILE and fields["cv"].required
        assert fields["exp"].options == ("0-2 years", "3-5 years", "6-9 years", "10+ years")
        assert (
            fields["fullName"].label == "Full name" and fields["fullName"].selector == "#fullName"
        )

    def test_step2_radio_group_and_optional(self) -> None:
        fields = {f.key: f for f in extract_fields((FIX / "step2.html").read_text())}
        assert fields["relocate"].kind is FieldKind.RADIO
        assert fields["relocate"].label == "Are you willing to relocate?"
        assert fields["relocate"].options == ("Yes", "No") and fields["relocate"].required
        assert not fields["cover"].required

    def test_buttons(self) -> None:
        assert [
            b.role for b in find_buttons((FIX / "posting.html").read_text(), in_form=False)
        ] == [ButtonRole.APPLY]
        assert (
            find_buttons((FIX / "step1.html").read_text(), in_form=True)[0].role is ButtonRole.NEXT
        )
        submit = find_buttons((FIX / "step2.html").read_text(), in_form=True)[0]
        assert submit.role is ButtonRole.SUBMIT and submit.selector == "#submit"

    def test_confirmation(self) -> None:
        assert "APP-20431" in confirmation_text((FIX / "done.html").read_text())
        assert confirmation_text((FIX / "step1.html").read_text()) is None


class TestChooseOption:
    @pytest.mark.parametrize(
        ("value", "options", "expected"),
        [
            ("7", ("0-2 years", "3-5 years", "6-9 years", "10+ years"), "6-9 years"),
            ("12", ("0-2 years", "10+ years"), "10+ years"),
            ("Yes, to Pune or Bengaluru.", ("Yes", "No"), "Yes"),
            ("no", ("Yes", "No"), "No"),
            ("2 months", ("Immediate", "1 month", "2 months"), "2 months"),
        ],
    )
    def test_matches(self, value: str, options: tuple, expected: str) -> None:
        assert choose_option(value, options)[0] == expected

    def test_no_match(self) -> None:
        assert choose_option("Kubernetes", ("Yes", "No")) is None


class TestAnswerer:
    def _answerer(self, session, **kw):  # noqa: ANN001, ANN003, ANN202
        return FieldAnswerer(
            PROFILE, answer_bank=AnswerBank(session), resume_path=Path("/r/cv.pdf"), **kw
        )

    def test_step1_all_known(self, session) -> None:
        a = self._answerer(session)
        got = {f.key: a.propose(f) for f in extract_fields((FIX / "step1.html").read_text())}
        assert got["fullName"].value == "Asha Rao" and got["fullName"].source == "profile"
        assert got["email"].source == "prefilled"
        assert got["cv"].value == "/r/cv.pdf"
        assert got["exp"].value == "6-9 years" and got["exp"].confidence == 0.9
        assert not any(p.needs_user(0.8) for p in got.values())

    def test_step2_unknown_required_goes_to_user(self, session) -> None:
        AnswerBank(session).upsert("Are you willing to relocate?", "Yes, to Pune", confirmed=True)
        a = self._answerer(session)
        got = {f.key: a.propose(f) for f in extract_fields((FIX / "step2.html").read_text())}
        assert got["notice"].value == "2 months"  # 60 days in the form's units
        assert got["relocate"].value == "Yes" and got["relocate"].source == "answer_bank"
        assert got["ectc"].value == "30"  # lakhs, as the label asks
        assert got["why"].needs_user(0.8)  # nothing grounded to say: ask, don't invent
        assert not got["cover"].needs_user(0.8) and got["cover"].value == ""  # optional: blank

    def test_rag_used_for_required_only_and_low_confidence_asks(self, session) -> None:
        calls: list[str] = []

        def rag(q: str) -> tuple[str, float]:
            calls.append(q)
            return ("I like payments", 0.5)

        a = self._answerer(session, rag=rag)
        got = {f.key: a.propose(f) for f in extract_fields((FIX / "step2.html").read_text())}
        # Required questions without a saved answer go to RAG; the optional cover letter doesn't.
        assert calls == ["Are you willing to relocate?", "Why do you want to join Acme?"]
        assert got["why"].needs_user(0.8) and got["why"].value == "I like payments"

    def test_override_wins(self, session) -> None:
        a = self._answerer(session, overrides={"why": "Your payments scale excites me."})
        [why] = [f for f in extract_fields((FIX / "step2.html").read_text()) if f.key == "why"]
        assert (
            a.propose(why).value == "Your payments scale excites me."
            and a.propose(why).source == "override"
        )

    def test_skill_years(self, session) -> None:
        from jobpilot.agents.application.forms import FormField

        f = FormField(
            key="k",
            selector="#k",
            label="Years of experience in Kafka",
            kind=FieldKind.NUMBER,
            required=True,
        )
        assert self._answerer(session).propose(f).value == "4"
