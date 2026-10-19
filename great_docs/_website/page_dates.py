"""
Date and author metadata for copied and generated pages

Copied pages carry their dates and author as `<head>` tags in front matter.
Generated pages store a marker and display the site build time.
Keeping that timestamp out of each generated page preserves its source
between builds, so Quarto can reuse frozen execution results.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit, urlunsplit

from . import head
from .seo import description

if TYPE_CHECKING:
    from ..config import Config

AUTO_GENERATED = "gd-auto-generated"
"""Front matter key for pages that display the site build time"""

_GITHUB_USER = re.compile(r"github\.com/([^/]+)")
_PORTRAITS = Path("_shared/authors")


def head_tags(source: Path, config: Config, authors: list[dict[str, Any]]) -> list[str]:
    """
    Build the date and author tags for a page copied from `source`

    Use `last_update.date` for the modified date, falling back to the last
    commit date and then the file modification time. Use `date_created` for
    the created date, falling back to the commit that added the file.
    Prefer `last_update.author` to `author`; look up missing portrait and
    profile details in the configured authors.

    Parameters
    ----------
    source
        The page's source file.
    config
        Project configuration. `show_dates` enables the tags; `show_author`
        enables author details.
    authors
        The configured authors, with local portrait paths replaced by site URLs.

    Returns
    -------
    :
        `<meta>` elements, or an empty list when `show_dates` is off or the
        source file is missing.
    """
    if not config.show_dates or not source.is_file():
        return []

    front_matter, _ = description.split_front_matter(source.read_text(encoding="utf-8"))
    front_matter = front_matter or {}
    last_update = front_matter.get("last_update")
    if not isinstance(last_update, dict):
        last_update = {}

    modified = _iso_date(last_update.get("date")) or _git_date(source, "-1")
    if not modified:
        modified = datetime.fromtimestamp(source.stat().st_mtime).isoformat()
    created = _iso_date(front_matter.get("date_created")) or _git_created(source)

    values = {"gd-page-modified": modified, "gd-page-created": created}
    if config.show_author:
        values.update(_author(front_matter, last_update, authors))

    return [head.meta_element(name, value) for name, value in values.items() if value]


def partial_lines(build_time: str) -> str:
    """
    Build template lines for generated pages and the site build time

    Parameters
    ----------
    build_time
        The site's build time as an ISO 8601 string.

    Returns
    -------
    :
        Template lines for the marker of generated pages and the site build
        time.
    """
    build_time = html.escape(build_time, quote=True).replace("$", "$$")
    return (
        f"$if({AUTO_GENERATED})$\n"
        f'<meta name="{AUTO_GENERATED}" content="true">\n'
        "$endif$\n"
        f'<meta name="gd-build-time" content="{build_time}">'
    )


def add_to_page(content: str, source: Path, config: Config, authors: list[dict[str, Any]]) -> str:
    """
    Add date and author tags to the `<head>` of a copied page

    Parameters
    ----------
    content
        The page as written to the build directory.
    source
        The page's source file.
    config
        Project configuration.
    authors
        The configured authors, with local portrait paths replaced by site URLs.

    Returns
    -------
    :
        The page with date and author tags, or the original content when
        `show_dates` is off or the source file is missing.
    """
    return head.add_tags(content, head_tags(source, config, authors))


def mark_generated(directory: Path, config: Config) -> None:
    """
    Mark every page under a directory as generated

    Parameters
    ----------
    directory
        A directory of the Quarto project, such as `reference/`.
    config
        Project configuration. Nothing is written when `show_dates` is off.
    """
    if config.show_dates:
        (directory / "_metadata.yml").write_text(f"{AUTO_GENERATED}: true\n", encoding="utf-8")


def site_authors(config: Config, source_dir: Path, base_url: str | None) -> list[dict[str, Any]]:
    """
    Replace local portrait paths with site URLs in the configured authors

    Parameters
    ----------
    config
        Project configuration.
    source_dir
        The directory that portrait paths are relative to.
    base_url
        The site's address, or `None` for a site served from `/`.

    Returns
    -------
    :
        The authors, with existing local portraits addressed under
        `_shared/authors/`. Query strings, fragments and other image
        references are unchanged.
    """
    authors: list[dict[str, Any]] = []
    for author in config.authors:
        portrait = _portrait(author, source_dir)
        if portrait is not None:
            url = urlsplit(author["image"])
            path = (base_url or "/") + (_PORTRAITS / portrait.name).as_posix()
            author = {**author, "image": urlunsplit(("", "", path, url.query, url.fragment))}
        authors.append(author)
    return authors


def copy_portraits(config: Config, source_dir: Path, build_dir: Path) -> None:
    """
    Copy the configured authors' local portraits to the site

    Parameters
    ----------
    config
        Project configuration. Nothing is copied when `show_dates` is off.
    source_dir
        The directory that portrait paths are relative to.
    build_dir
        The Quarto project directory.

    Raises
    ------
    ValueError
        If two different portraits share a file name.
    """
    if not config.show_dates:
        return
    for author in config.authors:
        portrait = _portrait(author, source_dir)
        if portrait is None:
            continue
        destination = build_dir / _PORTRAITS / portrait.name
        if destination.exists():
            if destination.read_bytes() != portrait.read_bytes():
                raise ValueError(f"Author portraits share the file name {portrait.name!r}")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(portrait, destination)


def _portrait(author: dict[str, Any], source_dir: Path) -> Path | None:
    """
    Find an author's local portrait

    Parameters
    ----------
    author
        A configured author.
    source_dir
        The directory that portrait paths are relative to.

    Returns
    -------
    :
        The existing local portrait file, or `None` when the image is missing
        or has a URL scheme or host.
    """
    image = author.get("image")
    if not isinstance(image, str):
        return None
    url = urlsplit(image)
    if url.scheme or url.netloc or not url.path:
        return None
    portrait = (source_dir / unquote(url.path)).resolve()
    return portrait if portrait.is_file() else None


def _iso_date(value: object) -> str:
    """
    Format a front matter date for a metadata tag

    Parameters
    ----------
    value
        A date, with or without a time.

    Returns
    -------
    :
        The date with midnight added when no time is present. Pass strings
        containing `T` through unchanged. Return an empty string for missing
        values or other values that cannot be parsed as dates.
    """
    if not value:
        return ""
    text = str(value)
    if "T" in text:
        return text
    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError:
        return ""


def _git_log(source: Path, *args: str) -> list[str]:
    """
    List the author dates of commits to a file

    Use the file's directory to select its repository.

    Parameters
    ----------
    source
        The file.
    *args
        Extra `git log` options.

    Returns
    -------
    :
        ISO 8601 dates in the requested Git log order, or an empty list if
        no commits match or Git cannot read the history.
    """
    try:
        result = subprocess.run(
            ["git", "log", *args, "--format=%aI", "--", source.name],
            cwd=source.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return result.stdout.split()


def _git_date(source: Path, *args: str) -> str:
    """
    Get the date of the last commit to a file

    Parameters
    ----------
    source
        The file.
    *args
        Extra `git log` options.

    Returns
    -------
    :
        The date, or an empty string for an untracked file.
    """
    dates = _git_log(source, *args)
    return dates[0] if dates else ""


def _git_created(source: Path) -> str:
    """
    Get the date of the commit that added a file

    Parameters
    ----------
    source
        The file.

    Returns
    -------
    :
        The date, or an empty string for an untracked file.
    """
    dates = _git_log(source, "--diff-filter=A", "--follow")
    return dates[-1] if dates else ""


def _author(
    front_matter: dict[str, Any], last_update: dict[str, Any], authors: list[dict[str, Any]]
) -> dict[str, str]:
    """
    Resolve a page's author tags

    Parameters
    ----------
    front_matter
        The page's front matter.
    last_update
        The page's `last_update` mapping.
    authors
        The configured authors.

    Returns
    -------
    :
        The author's name, image and URL, each empty when unknown.
    """
    name = str(last_update.get("author") or "")
    image = url = ""
    if not name:
        author = front_matter.get("author")
        if isinstance(author, str):
            name = author
        elif isinstance(author, dict):
            name = str(author.get("name") or "")
            image = str(author.get("image") or "")
            url = str(author.get("url") or "")

    if name and not image:
        entry = next(
            (a for a in authors if isinstance(a, dict) and a.get("name") == name),
            {},
        )
        homepage = str(entry.get("homepage") or "")
        image = str(entry.get("image") or "")
        if not image:
            if github := entry.get("github"):
                image = f"https://github.com/{github}.png"
            elif match := _GITHUB_USER.search(homepage):
                image = f"https://github.com/{match.group(1)}.png"
        url = url or homepage

    return {"gd-page-author": name, "gd-page-author-image": image, "gd-page-author-url": url}
