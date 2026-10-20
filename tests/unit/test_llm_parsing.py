from __future__ import annotations

import json
from enum import StrEnum

import pytest
from pydantic import BaseModel, ValidationError

from jobpilot.agents.profile.schema import CandidateProfile
from jobpilot.llm.json_extract import drop_nulls, extract_json_text, parse_model
from jobpilot.llm.schema_prompt import example_instance, schema_instruction


class Mode(StrEnum):
    A = "remote"
    B = "onsite"


class Inner(BaseModel):
    name: str
    years: float | None = None


class Outer(BaseModel):
    title: str = ""
    count: int = 0
    flag: bool = False
    mode: Mode | None = None
    items: list[Inner] = []


class TestExtract:
    @pytest.mark.parametrize(
        "content",
        [
            '{"a": 1}',
            '<think>reasoning {not json}</think>{"a": 1}',
            'Sure! ```json\n{"a": 1}\n``` hope that helps',
            'prefix {"a": 1} suffix',
        ],
    )
    def test_finds_the_object(self, content: str) -> None:
        assert json.loads(extract_json_text(content)) == {"a": 1}

    def test_drop_nulls_is_recursive_and_pure(self) -> None:
        data = {"a": None, "b": [None, {"c": None, "d": 1}], "e": 0, "f": ""}
        assert drop_nulls(data) == {"b": [{"d": 1}], "e": 0, "f": ""}
        assert data["a"] is None  # input untouched

    def test_nulls_fall_back_to_defaults(self) -> None:
        profile = parse_model(
            CandidateProfile, '{"full_name": "Asha", "email": null, "skills": null}'
        )
        assert (profile.full_name, profile.email, profile.skills) == ("Asha", "", [])

    def test_required_fields_still_enforced(self) -> None:
        with pytest.raises(ValidationError):
            parse_model(Outer, '{"items": [{"name": null}]}')


def test_skill_zero_years_means_unknown() -> None:
    from jobpilot.agents.profile.schema import Skill

    assert Skill(name="Java", years=0).years is None
    assert Skill(name="Python", years=7).years == 7


class TestSchemaPrompt:
    def test_example_has_every_key_and_enum_options(self) -> None:
        example = example_instance(Outer)
        assert example == {
            "title": "<text>",
            "count": 0,
            "flag": False,
            "mode": "remote | onsite",
            "items": [{"name": "<text>", "years": None}],
        }

    def test_profile_skeleton_resolves_nested_refs(self) -> None:
        example = example_instance(CandidateProfile)
        assert example["skills"] == [{"name": "<text>", "years": None}]
        assert example["work_modes"] == ["remote | hybrid | onsite"]
        assert set(example["current_ctc"]) == {"amount", "currency", "period"}

    def test_instruction_never_includes_raw_schema(self) -> None:
        text = schema_instruction(CandidateProfile)
        assert '"properties"' not in text and "$defs" not in text
        assert '"full_name"' in text
