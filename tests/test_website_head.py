"""
Tags for the `<head>` of raw pages and of pages that Quarto renders
"""

import pytest

from great_docs._website import head, page_metadata
from great_docs._website.seo.description import split_front_matter

FIRST_TAG = '<meta name="a" content="1">'
SECOND_TAG = '<meta name="b" content="2">'


def test_inserts_before_the_closing_head():
    page = head.insert("<head><title>T</title></head>", "<meta a>", unless="<meta a")
    assert page == "<head><title>T</title><meta a>\n</head>"


def test_leaves_a_page_that_has_the_marker():
    html = "<head><meta a></head>"
    assert head.insert(html, "<meta a>", unless="<meta a") == html


def test_leaves_a_page_without_a_head():
    assert head.insert("<div></div>", "<meta a>", unless="<meta a") == "<div></div>"


def test_add_tags_creates_front_matter() -> None:
    front_matter, body = split_front_matter(head.add_tags("Body\n", [FIRST_TAG]))
    assert front_matter == {"gd-head-tags": [f"`{FIRST_TAG}`{{=html}}"]}
    assert body == "\nBody\n"


def test_add_tags_keeps_the_author_front_matter() -> None:
    page = head.add_tags("---\n# comment\ntitle: T # inline\n---\n\nBody\n", [FIRST_TAG])
    front_matter, body = split_front_matter(page)
    assert page.startswith("---\ngd-head-tags:\n")
    assert "\n# comment\ntitle: T # inline\n---\n" in page
    assert front_matter == {"gd-head-tags": [f"`{FIRST_TAG}`{{=html}}"], "title": "T"}
    assert body == "\nBody\n"


def test_add_tags_merges_into_an_existing_list() -> None:
    page = head.add_tags("---\n# comment\ntitle: T\n---\n\nBody\n", [FIRST_TAG])
    page = page_metadata.add_to_page(page, {"description-meta": "D"})
    page = head.add_tags(page, [SECOND_TAG, FIRST_TAG])
    front_matter, _ = split_front_matter(page)
    assert page.count("gd-head-tags:") == 1
    assert "\n# comment\ntitle: T\n" in page
    assert front_matter == {
        "description-meta": "D",
        "gd-head-tags": [f"`{FIRST_TAG}`{{=html}}", f"`{SECOND_TAG}`{{=html}}"],
        "title": "T",
    }


def test_add_tags_rejects_a_hand_written_list() -> None:
    with pytest.raises(ValueError, match="gd-head-tags"):
        head.add_tags('---\ngd-head-tags: ["x"]\n---\n\nBody\n', [FIRST_TAG])


def test_tag_with_backticks_gets_a_longer_fence() -> None:
    tag = '<meta name="a" content="x`y">'
    front_matter, _ = split_front_matter(head.add_tags("Body\n", [tag]))
    assert front_matter == {"gd-head-tags": [f"``{tag}``{{=html}}"]}


def test_meta_element_escapes_attribute_content() -> None:
    assert head.meta_element("a", 'O\'Brien & "Co"') == (
        '<meta name="a" content="O&#x27;Brien &amp; &quot;Co&quot;">'
    )
