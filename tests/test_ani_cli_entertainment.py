"""Durable AniCLI menu parsing and exact old/new package contract admission."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from src.entertainment import schemas
from src.extension_cli_adapter import validate_cli_execution
from src.extension_installer import ExtensionLifecycleError
from src.extension_intake import Proposal

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "integrations/repository-packages/ani-cli"


def adapter():
    spec = importlib.util.spec_from_file_location(
        "ani_cli_entertainment", PACKAGE / "adapter.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def proposal():
    return json.loads((PACKAGE / "proposal.json").read_text())


def test_search_distinguishes_movie_titles_and_series_with_bounded_schema():
    items = adapter()._search_items(
        [
            "1 Naruto",
            "2 Naruto the Movie: Bonds",
            "3 Gekijouban Other",
            "4 劇場版 Anime",
            "noise",
        ]
    )
    assert [item["kind"] for item in items] == ["series", "movie", "movie", "movie"]
    assert items[1] == {"id": 2, "title": "Naruto the Movie: Bonds", "kind": "movie"}
    Draft202012Validator(schemas("entertainment.ani.search")[1]).validate(
        {"items": items}
    )


@pytest.mark.parametrize(
    "prompt", ["Select episode", "Playing episode 1", "PLAYING EPISODE 1"]
)
def test_episode_capture_retains_prompt_and_never_emits_player_controls(
    tmp_path, prompt
):
    (tmp_path / "capture.json").write_text(
        json.dumps(
            {
                "prompt": prompt,
                "items": [
                    "1",
                    "2",
                    "1.5",
                    "next",
                    "replay",
                    "previous",
                    "select",
                    "change_quality",
                    "quit",
                    "Episode next",
                ],
            }
        )
    )
    module = adapter()
    assert module._menu(tmp_path)["prompt"] == prompt
    assert module._episode_values(tmp_path) == (
        ["1", "2", "1.5"] if prompt == "Select episode" else []
    )


def test_control_filter_runs_before_episode_pagination(tmp_path):
    episodes = [str(number) for number in range(1, 202)]
    (tmp_path / "capture.json").write_text(
        json.dumps(
            {
                "prompt": "Select episode",
                "items": ["next", *episodes[:100], "quit", *episodes[100:]],
            }
        )
    )
    values = adapter()._episode_values(tmp_path)
    assert len(values) == 201
    assert values[100:200] == [str(number) for number in range(101, 201)]


@pytest.mark.parametrize("legacy", [False, True])
def test_current_and_exact_legacy_signed_contracts_are_accepted(legacy):
    data = proposal()
    interface = next(
        item
        for item in data["interfaces"]
        if item["binding"] == "entertainment.ani.search"
    )
    interface["output_schema"] = schemas(interface["binding"], legacy=legacy)[1]
    Proposal.model_validate(data)
    contract = validate_cli_execution(data["manifest"], data)
    assert (
        contract["interfaces"]["ani_cli__search"]["output_schema"]
        == interface["output_schema"]
    )


@pytest.mark.parametrize("legacy", [False, True])
def test_compatibility_rejects_any_broadening_of_old_or_current_schema(legacy):
    data = proposal()
    interface = next(
        item
        for item in data["interfaces"]
        if item["binding"] == "entertainment.ani.search"
    )
    interface["output_schema"] = copy.deepcopy(
        schemas(interface["binding"], legacy=legacy)[1]
    )
    interface["output_schema"]["properties"]["items"]["items"][
        "additionalProperties"
    ] = True
    with pytest.raises(
        ExtensionLifecycleError, match="extension_entertainment_contract_invalid"
    ):
        validate_cli_execution(data["manifest"], data)


def test_proposal_embeds_the_actual_reviewed_adapter_files():
    for file in proposal()["files"]:
        name = file["path"].rsplit("/", 1)[-1]
        assert file["content"] == (PACKAGE / name).read_text()
