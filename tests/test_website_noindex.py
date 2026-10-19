from __future__ import annotations

from types import SimpleNamespace

from yaml12 import parse_yaml

from great_docs._website import head
from great_docs._website.seo import noindex

PAGE_SOURCE = "---\ntitle: Skills\n---\n\nBody\n"
ROBOTS_TAG = '`<meta name="robots" content="noindex, nofollow">`{=html}'
EXISTING_TAG = '`<meta name="other" content="x">`{=html}'


def parse_front_matter(content: str) -> dict:
    return parse_yaml(content.split("---\n")[1])


def test_add_to_page_adds_robots_tag_with_seo() -> None:
    config = SimpleNamespace(seo_enabled=True)

    result = noindex.add_to_page(config, PAGE_SOURCE)

    assert parse_front_matter(result) == {head.HEAD_TAGS: [ROBOTS_TAG], "title": "Skills"}
    assert result.endswith("\n---\n\nBody\n")


def test_add_to_page_leaves_page_unchanged_without_seo() -> None:
    config = SimpleNamespace(seo_enabled=False)

    assert noindex.add_to_page(config, PAGE_SOURCE) == PAGE_SOURCE


def test_add_to_page_keeps_existing_head_tags() -> None:
    config = SimpleNamespace(seo_enabled=True)
    page = head.add_tags(PAGE_SOURCE, ['<meta name="other" content="x">'])

    result = noindex.add_to_page(config, page)

    assert parse_front_matter(result)[head.HEAD_TAGS] == [EXISTING_TAG, ROBOTS_TAG]


def test_add_to_page_adds_robots_tag_once() -> None:
    config = SimpleNamespace(seo_enabled=True)

    page_with_robots = noindex.add_to_page(config, PAGE_SOURCE)

    assert noindex.add_to_page(config, page_with_robots) == page_with_robots
