"""Bucket header layout guards: labels flush left at rest, handle overlays on
hover, and no empty-state copy under the Projects header."""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "static"


def test_bucket_handle_overlays_instead_of_reserving_space():
    css = (STATIC / "style.css").read_text()
    block = css.split("Sidebar bucket reorder handle", 1)[1][:1600]
    assert "position: absolute" in block
    assert "pointer-events: none" in block
    assert "translateX(17px)" in block
    assert "margin-right: 2px" not in block


def test_projects_empty_state_copy_removed():
    index = (STATIC / "index.html").read_text()
    projects = (STATIC / "js" / "projects.js").read_text()
    assert 'id="projects-empty"' not in index
    assert "projects-empty" not in projects
    assert "No projects yet" not in projects
