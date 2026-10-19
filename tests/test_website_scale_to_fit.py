"""
Page-level scale-to-fit tags resolved from front matter and directory metadata
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest

from great_docs._website.page_metadata import Page
from great_docs._website.scale_to_fit import Overrides, element


def _write(root: Path, relative_path: str, text: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _selectors(tag: str | None) -> list[str] | None:
    if tag is None:
        return None
    match = re.search(r'data-selectors="([^"]*)"', tag)
    assert match is not None
    return json.loads(html.unescape(match.group(1)))


def _tag(project: Path, page: Path) -> str | None:
    return Overrides.make(project).tag(Page.make(page, project))


def test_element_markup() -> None:
    assert element(["#t"], None) == (
        '<meta name="gd-scale-to-fit-page" data-selectors="[&quot;#t&quot;]">'
    )


def test_element_string_selector() -> None:
    assert _selectors(element("#t", None)) == ["#t"]


@pytest.mark.parametrize("selectors", [[1, None], [], None, False, {"a": "b"}, (".a",)])
def test_element_without_string_selectors(selectors: object) -> None:
    assert element(selectors, None) is None


def test_element_keeps_only_string_selectors() -> None:
    assert _selectors(element(["#a", 1, "", ".b"], None)) == ["#a", "", ".b"]


def test_element_escapes_selectors() -> None:
    selectors = ['div[data-x="a b"]', "x`y"]
    tag = element(selectors, None)
    assert tag is not None
    assert '"a b"' not in tag
    assert _selectors(tag) == selectors


@pytest.mark.parametrize(
    ("min_scale", "expected"),
    [(" Tablet ", "tablet"), (0.35, "0.35"), ("0.35", "0.35")],
)
def test_element_min_scale(min_scale: object, expected: str) -> None:
    tag = element(["#t"], min_scale)
    assert tag is not None
    assert f'data-min-scale="{expected}"' in tag


@pytest.mark.parametrize("min_scale", [0, 1, 1.5, True, False, "nan", "inf", "xlarge", ["tablet"]])
def test_element_ignores_an_invalid_min_scale(min_scale: object) -> None:
    assert element(["#t"], min_scale) == element(["#t"], None)


def test_directory_lists_concatenate_outermost_first(tmp_path: Path) -> None:
    _write(tmp_path, "_metadata.yml", 'scale-to-fit: [".root"]\n')
    _write(tmp_path, "A/_metadata.yml", 'scale-to-fit: [".a1", ".a2"]\n')
    _write(tmp_path, "A/B/_metadata.yml", 'scale-to-fit: ".b"\n')
    _write(tmp_path, "A/B/C/_metadata.yml", "scale-to-fit: null\n")
    page = _write(tmp_path, "A/B/C/page.qmd", "---\ntitle: P\n---\n\nBody\n")

    assert _selectors(_tag(tmp_path, page)) == [".root", ".a1", ".a2", ".b"]


@pytest.mark.parametrize(
    ("value", "expected"), [('[".p"]', [".p"]), ('".p"', [".p"]), ("[]", None), ("null", None)]
)
def test_page_key_replaces_directory_selectors(
    tmp_path: Path, value: str, expected: object
) -> None:
    _write(tmp_path, "A/_metadata.yml", 'scale-to-fit: [".a"]\n')
    page = _write(tmp_path, "A/page.md", f"---\nscale-to-fit: {value}\n---\n\nBody\n")

    assert _selectors(_tag(tmp_path, page)) == expected


@pytest.mark.parametrize(
    ("nearer", "expected_attribute"), [("0.3", ' data-min-scale="0.3"'), ("null", "")]
)
def test_nearest_directory_min_scale_wins(
    tmp_path: Path, nearer: str, expected_attribute: str
) -> None:
    _write(tmp_path, "A/_metadata.yml", 'scale-to-fit: [".a"]\nscale-to-fit-min-scale: tablet\n')
    _write(tmp_path, "A/C/_metadata.yml", f"scale-to-fit-min-scale: {nearer}\n")
    page = _write(tmp_path, "A/C/page.qmd", "Body\n")

    assert _tag(tmp_path, page) == (
        f'<meta name="gd-scale-to-fit-page" data-selectors="[&quot;.a&quot;]"{expected_attribute}>'
    )


@pytest.mark.parametrize(
    ("content", "expected"),
    [('scale-to-fit: [".yml"]\n', [".yml"]), ("[invalid", None), ("[]", None)],
)
def test_metadata_yml_shadows_metadata_yaml(
    tmp_path: Path, content: str, expected: list[str] | None
) -> None:
    _write(tmp_path, "A/_metadata.yml", content)
    _write(tmp_path, "A/_metadata.yaml", 'scale-to-fit: [".yaml"]\n')
    _write(tmp_path, "B/_metadata.yaml", 'scale-to-fit: [".yaml"]\n')
    a = _write(tmp_path, "A/page.qmd", "Body\n")
    b = _write(tmp_path, "B/page.qmd", "Body\n")

    assert _selectors(_tag(tmp_path, a)) == expected
    assert _selectors(_tag(tmp_path, b)) == [".yaml"]


def test_a_new_pass_sees_edited_metadata(tmp_path: Path) -> None:
    metadata = _write(tmp_path, "A/_metadata.yml", 'scale-to-fit: [".old"]\n')
    page = _write(tmp_path, "A/page.qmd", "Body\n")
    assert _selectors(_tag(tmp_path, page)) == [".old"]

    metadata.write_text('scale-to-fit: [".new"]\n', encoding="utf-8")
    assert _selectors(_tag(tmp_path, page)) == [".new"]
