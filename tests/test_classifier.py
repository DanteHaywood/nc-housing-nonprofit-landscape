import os
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pandas as pd
import pytest

from src.classify.classifier import (
    PREDICTION_COLUMNS,
    SHEET_ENCODING,
    STATUS_API_ERROR,
    STATUS_INVALID,
    STATUS_NO_TOOL,
    STATUS_OK,
    STATUS_REFUSAL,
    TOOL_NAME,
    cache_path,
    classify_claude,
    classify_random,
    label_tool,
    load_prompt,
    load_sheet,
    parse_response,
    prediction_path,
    read_cache,
    render_prompt,
    uncached_eins,
    write_predictions,
)
from src.classify.run_dev import calls_allowed
from src.config import load_env

FIXTURES = Path(__file__).parent / "fixtures"
# Three organizations laid out like evals/labels/labels_dev.csv (BOM included).
# The third EIN is 8 digits, as Excel leaves it after dropping a leading zero.
SHEET = FIXTURES / "labels_sheet_sample.csv"
LABELS = [
    "emergency_shelter",
    "affordable_housing_development",
    "homeownership",
    "rental_assistance",
    "supportive_housing",
    "not_housing",
]


def tool_response(label: str, reason: str = "Programs build housing.") -> dict:
    """A response dict shaped like Message.to_dict() with one tool call."""
    return {
        "stop_reason": "tool_use",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": TOOL_NAME,
                "input": {"reason": reason, "label": label},
            }
        ],
    }


class FakeMessage:
    def __init__(self, data: dict) -> None:
        self.data = data

    def to_dict(self) -> dict:
        return self.data


class FakeClient:
    """Stands in for anthropic.Anthropic: answers from a label per EIN."""

    def __init__(self, labels_by_ein: dict[str, str], fail: set[str] = frozenset()):
        self.labels_by_ein = labels_by_ein
        self.fail = fail
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **params: Any) -> FakeMessage:
        self.calls.append(params)
        prompt = params["messages"][0]["content"]
        ein = next(e for e in self.labels_by_ein if f"[{e}]" in prompt)
        if ein in self.fail:
            request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            raise anthropic.APIConnectionError(request=request)
        return FakeMessage(tool_response(self.labels_by_ein[ein]))


@pytest.fixture
def sheet() -> pd.DataFrame:
    df = load_sheet(SHEET)
    # Tag each name with its EIN so FakeClient can tell organizations apart.
    df["name"] = df["name"] + " [" + df["ein"] + "]"
    return df


@pytest.fixture
def config(tmp_path: Path) -> dict[str, Any]:
    return {
        "model": "claude-test-model",
        "paths": {
            "interim": str(tmp_path / "interim"),
            "processed": str(tmp_path / "processed" / "landscape.duckdb"),
            "labels": str(FIXTURES),
        },
        "housing_labels": LABELS,
        "random_baseline_seed": 7,
    }


ANSWERS = {
    "474942066": "supportive_housing",
    "920731806": "rental_assistance",
    "055555555": "not_housing",
}


# The sheet loads with EINs padded, and a sheet missing a text column is rejected.
def test_load_sheet(tmp_path: Path) -> None:
    df = load_sheet(SHEET)
    assert df["ein"].tolist() == ["474942066", "920731806", "055555555"]
    broken = tmp_path / "broken.csv"
    df.drop(columns=["programs"]).to_csv(broken, index=False, encoding=SHEET_ENCODING)
    with pytest.raises(ValueError, match="missing columns: programs"):
        load_sheet(broken)


# A repeated EIN would make two predictions share one cache key.
def test_load_sheet_rejects_repeated_ein(tmp_path: Path) -> None:
    df = load_sheet(SHEET)
    doubled = tmp_path / "doubled.csv"
    pd.concat([df, df.head(1)]).to_csv(doubled, index=False, encoding=SHEET_ENCODING)
    with pytest.raises(ValueError, match="more than once"):
        load_sheet(doubled)


# The real v001 prompt fills every placeholder and keeps its six categories.
def test_render_v001() -> None:
    template = load_prompt("v001")
    row = load_sheet(SHEET).iloc[1]
    prompt = render_prompt(template, row)
    assert "{{" not in prompt
    assert "<mission>See Schedule O</mission>" in prompt
    assert "Rent and utility help for 30 families." in prompt
    assert [f"<label>{label}</label>" in prompt for label in LABELS] == [True] * 6
    assert TOOL_NAME in prompt


# The tool is strict, asks for the reason before the label, and allows only
# the taxonomy's labels.
def test_label_tool() -> None:
    tool = label_tool(LABELS)
    schema = tool["input_schema"]
    assert tool["strict"] is True
    assert list(schema["properties"]) == ["reason", "label"]
    assert schema["properties"]["label"]["enum"] == LABELS
    assert schema["additionalProperties"] is False


# Each way a response can fail gets its own status; a bad label is kept as
# written, not coerced.
def test_parse_response() -> None:
    assert parse_response(tool_response("homeownership"), LABELS) == (
        "homeownership",
        "Programs build housing.",
        STATUS_OK,
    )
    assert parse_response(tool_response("housing_advocacy"), LABELS) == (
        "housing_advocacy",
        "Programs build housing.",
        STATUS_INVALID,
    )
    assert parse_response(tool_response("not_housing", reason=""), LABELS)[2] == (
        STATUS_INVALID
    )
    text_only = {"stop_reason": "end_turn", "content": [{"type": "text"}]}
    assert parse_response(text_only, LABELS) == ("", "", STATUS_NO_TOOL)
    refused = {"stop_reason": "refusal", "content": []}
    assert parse_response(refused, LABELS) == ("", "", STATUS_REFUSAL)


# The request forces the tool and uses the model from config.
def test_request_uses_config_model_and_forced_tool(
    sheet: pd.DataFrame, config: dict[str, Any]
) -> None:
    client = FakeClient(ANSWERS)
    classify_claude(sheet, config, "v001", client=client)
    params = client.calls[0]
    assert params["model"] == "claude-test-model"
    assert params["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert "temperature" not in params


# First run calls the API once per organization and caches each response;
# the second run makes no calls; --refresh calls again.
def test_cache_round_trip(sheet: pd.DataFrame, config: dict[str, Any]) -> None:
    first = FakeClient(ANSWERS)
    out = classify_claude(sheet, config, "v001", client=first)
    assert len(first.calls) == 3
    assert list(out.columns) == PREDICTION_COLUMNS
    assert dict(zip(out["ein"], out["predicted"], strict=True)) == ANSWERS
    assert set(out["status"]) == {STATUS_OK}

    cached = read_cache(cache_path(config, "v001", "claude-test-model"))
    assert set(cached) == set(ANSWERS)
    assert cached["474942066"]["prompt_version"] == "v001"
    assert uncached_eins(sheet, config, "v001") == []

    second = FakeClient(ANSWERS)
    again = classify_claude(sheet, config, "v001", client=second)
    assert second.calls == []
    pd.testing.assert_frame_equal(out, again)

    refreshed = FakeClient(ANSWERS)
    classify_claude(sheet, config, "v001", client=refreshed, refresh=True)
    assert len(refreshed.calls) == 3


# An API error is reported in the output and not cached, so a rerun retries it.
def test_api_error_is_not_cached(sheet: pd.DataFrame, config: dict[str, Any]) -> None:
    out = classify_claude(
        sheet, config, "v001", client=FakeClient(ANSWERS, fail={"920731806"})
    )
    statuses = dict(zip(out["ein"], out["status"], strict=True))
    assert statuses["920731806"] == STATUS_API_ERROR
    assert uncached_eins(sheet, config, "v001") == ["920731806"]


# The random baseline is reproducible and only uses taxonomy labels.
def test_classify_random(sheet: pd.DataFrame) -> None:
    first = classify_random(sheet, LABELS, seed=7)
    second = classify_random(sheet, LABELS, seed=7)
    pd.testing.assert_frame_equal(first, second)
    assert set(first["predicted"]) <= set(LABELS)
    assert first["classifier"].iloc[0] == "random_seed7"


# Output lands next to the DuckDB file, named for the sheet and classifier.
def test_write_predictions(sheet: pd.DataFrame, config: dict[str, Any]) -> None:
    out = classify_claude(sheet, config, "v001", client=FakeClient(ANSWERS))
    path = write_predictions(out, prediction_path(config, SHEET, out))
    assert path == (
        Path(config["paths"]["processed"]).parent
        / "labels_sheet_sample__claude__v001__claude-test-model.csv"
    )
    back = pd.read_csv(path, dtype=str, keep_default_na=False, encoding=SHEET_ENCODING)
    assert back["ein"].tolist() == out["ein"].tolist()

    rand = classify_random(sheet, LABELS, seed=7)
    assert prediction_path(config, SHEET, rand).name == (
        "labels_sheet_sample__random_seed7.csv"
    )


# More than 20 new calls needs --yes (CLAUDE.md).
def test_calls_allowed() -> None:
    assert calls_allowed(20, yes=False)
    assert not calls_allowed(21, yes=False)
    assert calls_allowed(40, yes=True)


# .env fills a missing variable but never overrides one set in the shell.
def test_load_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text("NCH_TEST_FROM_FILE=file\nNCH_TEST_SHELL=file\n", encoding="utf-8")
    monkeypatch.delenv("NCH_TEST_FROM_FILE", raising=False)
    monkeypatch.setenv("NCH_TEST_SHELL", "shell")
    assert load_env(env) is True
    assert os.environ["NCH_TEST_FROM_FILE"] == "file"
    assert os.environ["NCH_TEST_SHELL"] == "shell"
    monkeypatch.delenv("NCH_TEST_FROM_FILE")
    assert load_env(tmp_path / "missing.env") is False
