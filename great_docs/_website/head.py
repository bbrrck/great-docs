"""
The `<head>` of built pages: front matter tags, header includes and raw pages

Pages that Quarto renders list extra `<head>` elements under `HEAD_TAGS` in
their front matter. Site-wide header includes apply to every rendered page.
Raw custom pages bypass Quarto, so tags are added to them directly.
"""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING, Any

from yaml12 import format_yaml, parse_yaml

if TYPE_CHECKING:
    from pathlib import Path

HEAD_TAGS = "gd-head-tags"
"""Front matter key for tags that the metadata partial adds to a page's `<head>`"""

_BACKTICKS = re.compile("`+")


def meta_element(name: str, content: str) -> str:
    """
    Build a `<meta>` element with an escaped `content` value

    Parameters
    ----------
    name
        The `name` attribute, used as given.
    content
        The `content` attribute, escaped for a quoted attribute value.

    Returns
    -------
    :
        The element as one line of HTML.
    """
    return f'<meta name="{name}" content="{html.escape(content, quote=True)}">'


def add_tags(content: str, tags: list[str]) -> str:
    """
    Add elements to the `<head>` of a page that Quarto renders

    Store each element as inline raw HTML in the page's `HEAD_TAGS` list,
    after any elements already there. Every other front matter line,
    comments included, is kept as it is.

    Parameters
    ----------
    content
        The page source.
    tags
        The elements as single-line HTML markup. Elements already in the
        list are skipped.

    Returns
    -------
    :
        The page with the elements added, creating front matter if needed,
        or `content` unchanged when there is nothing new to add.

    Raises
    ------
    ValueError
        If the existing `HEAD_TAGS` value is not a list, or new elements
        cannot be merged into a list in the generated YAML style.
    """
    closing_offset, front_matter = _front_matter(content)
    existing = front_matter.get(HEAD_TAGS) if isinstance(front_matter, dict) else None
    if existing is not None and not isinstance(existing, list):
        raise ValueError(f"`{HEAD_TAGS}` in the front matter is not a list")
    existing_tags = existing or []
    new_tags = list(
        dict.fromkeys(tag for tag in map(_raw_inline, tags) if tag not in existing_tags)
    )
    if not new_tags:
        return content
    if existing is None:
        block = format_yaml({HEAD_TAGS: new_tags}).rstrip()
        if closing_offset:
            return f"---\n{block}\n{content[4:]}"
        return f"---\n{block}\n---\n\n{content}"

    # Match the generated block exactly to preserve surrounding YAML and comments.
    old_block = "\n" + format_yaml({HEAD_TAGS: existing}).rstrip() + "\n"
    block_offset = content.find(old_block, 3, closing_offset)
    if block_offset == -1:
        raise ValueError(f"Cannot add to a hand-written `{HEAD_TAGS}` in the front matter")
    new_block = "\n" + format_yaml({HEAD_TAGS: [*existing_tags, *new_tags]}).rstrip() + "\n"
    return content[:block_offset] + new_block + content[block_offset + len(old_block) :]


def _front_matter(content: str) -> tuple[int, Any]:
    """
    Locate and parse a page's YAML front matter

    Parameters
    ----------
    content
        The page source.

    Returns
    -------
    :
        The offset of the closing `---`, or 0 when the page has no front
        matter, and the parsed front matter, or `None`
        when there is none or it is not valid YAML.
    """
    if not content.startswith("---\n"):
        return 0, None
    end = content.find("\n---", 3)
    if end == -1:
        return 0, None
    try:
        return end + 1, parse_yaml(content[4 : end + 1])
    except ValueError:
        return end + 1, None


def _raw_inline(markup: str) -> str:
    """
    Wrap markup as an inline raw HTML span for Quarto Markdown

    Parameters
    ----------
    markup
        HTML that starts with `<` and ends with `>`.

    Returns
    -------
    :
        The markup in a backtick fence one longer than its longest run of
        backticks, so the markup needs no escaping.
    """
    fence = "`" * (max(map(len, _BACKTICKS.findall(markup)), default=0) + 1)
    return f"{fence}{markup}{fence}{{=html}}"


def search_includes(
    includes: Any, base_dir: Path, pattern: re.Pattern[str]
) -> re.Match[str] | None:
    """
    Find the first match for `pattern` in an `include-in-header` value

    Parameters
    ----------
    includes
        An `include-in-header` value: a file path, an entry with `text` or
        `file`, or a list of them.
    base_dir
        Base directory for relative header file paths.
    pattern
        The markup to look for.

    Returns
    -------
    :
        The match in an inline entry or a readable header file, or `None`.
    """
    if not isinstance(includes, list):
        includes = [includes]
    for item in includes:
        if isinstance(item, dict):
            text = item.get("text")
            path = item.get("file")
        else:
            text, path = None, item
        if text is None and path is not None:
            try:
                text = (base_dir / str(path)).read_text(encoding="utf-8")
            except OSError:
                continue
        if isinstance(text, str) and (match := pattern.search(text)):
            return match
    return None


def site_includes(quarto_config: dict[str, Any]) -> Any:
    """
    Return the site's `include-in-header` value

    Parameters
    ----------
    quarto_config
        Contents of `_quarto.yml`.

    Returns
    -------
    :
        The value, or an empty list when the site has none.
    """
    return quarto_config.get("format", {}).get("html", {}).get("include-in-header", [])


def includes_match(
    quarto_config: dict[str, Any], project_dir: Path, pattern: re.Pattern[str]
) -> bool:
    """
    Check whether the site's header includes contain a match for `pattern`

    Parameters
    ----------
    quarto_config
        Contents of `_quarto.yml`.
    project_dir
        Base directory for relative header file paths.
    pattern
        The markup to look for.

    Returns
    -------
    :
        `True` when an inline entry or a readable header file matches.
    """
    return search_includes(site_includes(quarto_config), project_dir, pattern) is not None


def page_includes_match(
    front_matter: dict[str, Any], page_dir: Path, pattern: re.Pattern[str]
) -> bool:
    """
    Check whether a page's own header includes contain a match for `pattern`

    Parameters
    ----------
    front_matter
        The page's front matter.
    page_dir
        Base directory for the page's relative header file paths.
    pattern
        The markup to look for.

    Returns
    -------
    :
        `True` when its `include-in-header`, its `format.html` one, or its
        `header-includes` matches.
    """
    page_format = front_matter.get("format")
    html_format = page_format.get("html") if isinstance(page_format, dict) else None
    for includes in (
        front_matter.get("include-in-header"),
        html_format.get("include-in-header") if isinstance(html_format, dict) else None,
    ):
        if includes is not None and search_includes(includes, page_dir, pattern):
            return True
    texts = front_matter.get("header-includes")
    if not isinstance(texts, list):
        texts = [texts]
    return any(isinstance(text, str) and pattern.search(text) for text in texts)


def insert(html: str, tag: str, *, unless: str) -> str:
    """
    Insert a tag at the end of a page's `<head>`

    Parameters
    ----------
    html
        The page source.
    tag
        The element to insert.
    unless
        A string whose presence in the page means the tag already exists.

    Returns
    -------
    :
        The page with the tag added, or `html` unchanged when it contains
        `unless` or has no `</head>`.
    """
    if unless in html or "</head>" not in html:
        return html
    return html.replace("</head>", f"{tag}\n</head>", 1)
