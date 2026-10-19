"""
Resolve page-level scale-to-fit tags before Quarto rendering

Page front matter and directory metadata override the site-wide selectors.
The resolved values become a `gd-scale-to-fit-page` `<meta>` tag.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from yaml12 import read_yaml

if TYPE_CHECKING:
    from pathlib import Path

    from .page_metadata import Page

SELECTORS = "scale-to-fit"
"""Front matter key for the selectors of the elements to scale"""

MIN_SCALE = "scale-to-fit-min-scale"
"""Front matter key for the smallest scale, as a keyword or a fraction"""

_KEYWORDS = ("mobile", "tablet", "desktop")


def element(selectors: object, min_scale: object) -> str | None:
    """
    Build the page's scale-to-fit `<meta>` tag from resolved values

    Parameters
    ----------
    selectors
        A CSS selector or a list of them. Items that are not strings are
        dropped.
    min_scale
        `mobile`, `tablet` or `desktop` in any case, or a number strictly
        between 0 and 1, possibly as a string. Any other value sets no
        minimum and leaves the selectors in force.

    Returns
    -------
    :
        The tag as one line of HTML, or `None` when no string selector
        remains.
    """
    if isinstance(selectors, str):
        selectors = [selectors]
    if not isinstance(selectors, list):
        return None
    selectors = [selector for selector in selectors if isinstance(selector, str)]
    if not selectors:
        return None

    selectors_json = html.escape(json.dumps(selectors, separators=(",", ":")), quote=True)
    minimum = _min_scale(min_scale)
    minimum_attr = f' data-min-scale="{minimum}"' if minimum else ""
    return f'<meta name="gd-scale-to-fit-page" data-selectors="{selectors_json}"{minimum_attr}>'


def _min_scale(value: object) -> str | None:
    """
    Normalise a minimum scale to its attribute value

    Parameters
    ----------
    value
        The resolved minimum scale.

    Returns
    -------
    :
        The lowercased keyword or the fraction as text, or `None` when the
        value is neither.
    """
    if isinstance(value, str):
        keyword = value.strip().lower()
        if keyword in _KEYWORDS:
            return keyword
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        fraction = float(value)
    except ValueError:
        return None
    return str(fraction) if 0 < fraction < 1 else None


@dataclass(frozen=True)
class Overrides:
    """
    Page overrides with cached directory metadata for one project

    Values are resolved as Quarto merges them. Directory `_metadata.yml`
    files apply outermost first: their selector lists concatenate, and the
    nearest minimum scale wins, including `null`. Page front matter replaces
    inherited values, including selector lists. Create a new instance to
    read subsequent metadata edits.
    """

    project_dir: Path
    """The Quarto project directory"""

    _metadata_by_directory: dict[Path, dict[str, Any]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    """Metadata read so far, by directory"""

    @classmethod
    def make(cls, project_dir: Path) -> Overrides:
        """
        Start resolving the overrides of a project

        Parameters
        ----------
        project_dir
            The Quarto project directory.

        Returns
        -------
        :
            Overrides that have read no metadata yet.
        """
        return cls(project_dir)

    def tag(self, page: Page) -> str | None:
        """
        Build a page's scale-to-fit `<meta>` tag

        Parameters
        ----------
        page
            A page under the project directory.

        Returns
        -------
        :
            The tag as one line of HTML, or `None` when the page resolves to
            no selectors.
        """
        parent = page.path.parent.relative_to(self.project_dir)
        directories = (*reversed(parent.parents), parent)
        selectors: list[Any] = []
        min_scale = None
        for directory in directories:
            metadata = self._metadata(self.project_dir / directory)
            value = metadata.get(SELECTORS)
            if isinstance(value, list):
                selectors.extend(value)
            elif value is not None:
                selectors.append(value)
            min_scale = metadata.get(MIN_SCALE, min_scale)

        return element(
            page.front_matter.get(SELECTORS, selectors),
            page.front_matter.get(MIN_SCALE, min_scale),
        )

    def _metadata(self, directory: Path) -> dict[str, Any]:
        """
        Read and cache a directory's metadata

        Parameters
        ----------
        directory
            A directory in the project.

        Returns
        -------
        :
            The metadata mapping, or an empty mapping if the file is absent,
            unreadable, invalid or holds a non-mapping value. `_metadata.yml`
            takes precedence over `_metadata.yaml`, even if reading it fails.
        """
        if directory not in self._metadata_by_directory:
            metadata: Any = None
            for name in ("_metadata.yml", "_metadata.yaml"):
                if (path := directory / name).is_file():
                    try:
                        metadata = read_yaml(path)
                    except (OSError, ValueError):
                        pass
                    break
            self._metadata_by_directory[directory] = metadata if isinstance(metadata, dict) else {}
        return self._metadata_by_directory[directory]
