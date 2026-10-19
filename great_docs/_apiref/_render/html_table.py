from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING, Protocol, Sequence

if TYPE_CHECKING:

    class Stringable(Protocol):
        def __str__(self) -> str: ...


def _md_link_to_html(text: str) -> str:
    """
    Convert markdown links to HTML anchor tags

    Handles the pandoc-style link format: [content](url){.class1 .class2}
    """
    # Pattern: [content](url){.class1 .class2} or [content](url)
    pattern = r"\[([^\]]+)\]\(([^)]+)\)(?:\{([^}]+)\})?"

    def replace_link(match: re.Match[str]) -> str:
        content = match.group(1)
        url = match.group(2)
        attr_str = match.group(3) or ""

        # Parse classes from attr string (e.g., ".doc-function .doc-label")
        classes: list[str] = []
        for part in attr_str.split():
            if part.startswith("."):
                classes.append(part[1:])

        class_attr = f' class="{" ".join(classes)}"' if classes else ""
        return f'<a href="{html.escape(url)}"{class_attr}>{content}</a>'

    return re.sub(pattern, replace_link, text)


def html_table(
    rows: Sequence[tuple[Stringable, Stringable | None]] | Sequence[Sequence[str]],
    *,
    headers: Sequence[str] | None = None,
    col_widths: Sequence[int] | None = None,
    table_class: str = "gd-summary-table",
) -> str:
    """
    Render rows as an HTML table.

    When `headers=` is provided the table switches to **multi-column mode**: the thead row is
    visible, optional per-column widths are applied via inline `style="width: X%"` on each `<th>`
    (the CSS class must set `table-layout: fixed`), and Bootstrap's base `caption-top table` classes
    are added alongside `table_class=` so the output matches what Quarto emits for Markdown tables.

    When `headers=` is `None` the function operates in legacy two-column mode: rows are
    `(name, description)` tuples, the `name` cell undergoes Markdown-link-to-HTML conversion, and
    styling is handled entirely by `table_class=` (default `gd-summary-table`).

    Parameters
    ----------
    rows
        Multi-column mode: sequence of rows, each a sequence of HTML-ready cell strings (caller is
        responsible for `html.escape` on text values). Legacy mode: sequence of
        `(name, description)` tuples where `name` may contain Pandoc-style Markdown links.
    headers
        Column header labels (plain text). Providing this activates multi-column mode.
    col_widths
        Integer percentage widths for each column (multi-column mode only). When given, each `<th>`
        gets an inline `style="width: X%"`. The CSS class must set `table-layout: fixed` for the
        widths to take effect.
    table_class
        Extra CSS class applied to the `<table>` element.

    Returns
    -------
    str
        HTML string with table markup.
    """
    if headers is not None:
        # ── Multi-column mode ──────────────────────────────────────────
        # Mirror the classes Quarto emits for markdown tables so Bootstrap
        # base styles apply, then tack on the caller's custom class.
        full_class = f"caption-top table {table_class}"

        if col_widths is not None:
            header_cells = "".join(
                f'<th style="width: {w}%">{html.escape(h)}</th>'
                for h, w in zip(headers, col_widths)
            )
        else:
            header_cells = "".join(f"<th>{html.escape(h)}</th>" for h in headers)

        body_rows = [
            "  <tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
        ]

        return (
            f'<table class="{full_class}" data-quarto-disable-processing="true">\n'
            f'<thead>\n  <tr class="header">{header_cells}</tr>\n</thead>\n'
            f"<tbody>\n" + "\n".join(body_rows) + "\n</tbody>\n</table>"
        )

    # ── Legacy two-column mode ─────────────────────────────────────────
    # Styling is handled by the .gd-summary-table class in great-docs.scss,
    # which overrides Bootstrap defaults for a cleaner appearance.
    body_rows_2col: list[str] = []
    for name, desc in rows:
        name, desc = str(name), str(desc) if desc is not None else ""
        # Convert markdown links to HTML
        name = _md_link_to_html(name)

        # Normalize description: join multi-line, strip excess whitespace
        if desc:
            desc = " ".join(line.strip() for line in desc.split("\n") if line.strip())
        else:
            desc = ""
        body_rows_2col.append(f"  <tr>\n    <td>{name}</td>\n    <td>{desc}</td>\n  </tr>")

    table_body = "\n".join(body_rows_2col)

    return f"""<table class="{table_class}">
<thead>
  <tr>
    <th>Name</th>
    <th>Description</th>
  </tr>
</thead>
<tbody>
{table_body}
</tbody>
</table>"""
