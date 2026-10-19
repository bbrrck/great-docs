"""
The robots directive that keeps generated pages out of search results
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from great_docs._website import head

if TYPE_CHECKING:
    from great_docs.config import Config

TAG = head.meta_element("robots", "noindex, nofollow")
"""Robots directive requesting no indexing or link following"""


def add_to_page(config: Config, content: str) -> str:
    """
    Add a noindex robots directive when SEO is enabled

    Parameters
    ----------
    config
        The site configuration.
    content
        The page source.

    Returns
    -------
    :
        The page source with the robots directive in its front matter,
        or `content` unchanged when SEO is disabled.
    """
    return head.add_tags(content, [TAG]) if config.seo_enabled else content
