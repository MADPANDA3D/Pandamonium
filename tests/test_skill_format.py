"""Focused checks for SKILL.md frontmatter parsing (MAD-953)."""

from services.memory.skill_format import Skill, parse_frontmatter


def test_folded_block_scalar_description_is_folded():
    text = (
        "---\n"
        "name: folded-skill\n"
        "description: >\n"
        "  Line one\n"
        "  line two.\n"
        "\n"
        "  Second paragraph.\n"
        "version: 1.0.0\n"
        "---\n\n"
        "## Procedure\n\n1. Do the thing.\n"
    )

    metadata, body = parse_frontmatter(text)

    assert metadata["name"] == "folded-skill"
    assert metadata["description"] == "Line one line two.\n\nSecond paragraph."
    assert metadata["version"] == "1.0.0"
    assert "Do the thing." in body


def test_literal_block_scalar_keeps_newlines():
    text = (
        "---\n"
        "name: literal-skill\n"
        "description: |-\n"
        "  First line\n"
        "  second line\n"
        "---\n\nBody\n"
    )

    metadata, _body = parse_frontmatter(text)

    assert metadata["description"] == "First line\nsecond line"


def test_block_scalar_skill_roundtrips_through_from_markdown():
    text = (
        "---\n"
        "name: folded-skill\n"
        "description: >\n"
        "  Folded summary line one\n"
        "  and line two.\n"
        "---\n\n"
        "## Procedure\n\n1. Do the thing.\n"
    )

    skill = Skill.from_markdown(text)

    assert skill.name == "folded-skill"
    assert skill.description == "Folded summary line one and line two."


def test_publisher_metadata_keys_with_hyphens_parse():
    text = (
        "---\n"
        "name: hyphen-skill\n"
        "description: Plain summary\n"
        'argument-hint: "[lite|full|ultra]"\n'
        "license: MIT\n"
        "---\n\nBody\n"
    )

    metadata, _body = parse_frontmatter(text)

    assert metadata["argument-hint"] == "[lite|full|ultra]"
    assert metadata["license"] == "MIT"


def test_inline_scalars_and_lists_still_parse():
    text = (
        "---\n"
        "name: plain-skill\n"
        'description: "Quoted summary"\n'
        "tags: [alpha, beta]\n"
        "platforms:\n"
        "  - linux\n"
        "  - macos\n"
        "---\n\nBody\n"
    )

    metadata, _body = parse_frontmatter(text)

    assert metadata["description"] == "Quoted summary"
    assert metadata["tags"] == ["alpha", "beta"]
    assert metadata["platforms"] == ["linux", "macos"]
