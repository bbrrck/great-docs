from __future__ import annotations

import glob
import html
import json
import os
import re
import sys


def _configure_stdio_for_unicode() -> None:
    """Use UTF-8 for console output when the platform default is narrow (e.g. cp1252)."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


_configure_stdio_for_unicode()

try:
    from great_docs._subprocess import TEXT_MODE_KWARGS as _SUBPROCESS_TEXT_KWARGS
except ImportError:
    _SUBPROCESS_TEXT_KWARGS = {"text": True, "encoding": "utf-8", "errors": "replace"}

# Skip post-render processing during freeze-only renders
if os.environ.get("GD_FREEZE_ONLY"):
    print("Skipping post-render (freeze-only mode)")
    raise SystemExit(0)

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import PythonLexer

# Load object type metadata for accurate classification
# Keys are object names (e.g., "parser.ParserError"), values are type strings
# ("class", "namedtuple", "typeddict", "protocol", "abc", "exception", "function", "method", "constant", "enum", "type_alias", "other")
object_types = {}
object_types_path = "_object_types.json"
if os.path.exists(object_types_path):
    print(f"Loading object types from {object_types_path}")
    with open(object_types_path, "r", encoding="utf-8") as f:
        object_types = json.load(f)
    print(f"Loaded {len(object_types)} object type entries")
else:
    print("No object types file found, falling back to heuristic classification")

# Load constant value metadata for displaying values on constant reference pages.
# Keys are constant names, values are dicts with optional "value" and "annotation".
constant_values: dict[str, dict[str, str]] = {}
constant_values_path = "_constant_values.json"
if os.path.exists(constant_values_path):
    with open(constant_values_path, "r", encoding="utf-8") as f:
        constant_values = json.load(f)
    print(f"Loaded {len(constant_values)} constant value entries")

# Load great-docs options (written by core.py during build)
_gd_options: dict[str, object] = {}
_gd_options_path = "_gd_options.json"
if os.path.exists(_gd_options_path):
    with open(_gd_options_path, "r", encoding="utf-8") as f:
        _gd_options = json.load(f)

# i18n helper — look up a translated string from _gd_options["i18n"]
_i18n_bundle: dict[str, str] = _gd_options.get("i18n", {})


def _t(key: str, fallback: str | None = None) -> str:
    """Look up a translated string, falling back to English default."""
    return _i18n_bundle.get(key, fallback if fallback is not None else key)


# Pygments class to Quarto class mapping
# Quarto uses different class names than Pygments default
PYGMENTS_TO_QUARTO_CLASS = {
    "n": "va",  # Name -> variable (generic names)
    "nc": "fu",  # Name.Class -> function (we want class names highlighted)
    "nf": "fu",  # Name.Function -> function
    "fm": "fu",  # Name.Function.Magic -> function
    "nb": "bu",  # Name.Builtin -> builtin
    "bp": "bu",  # Name.Builtin.Pseudo -> builtin
    "k": "kw",  # Keyword -> keyword
    "kc": "cn",  # Keyword.Constant -> constant (None, True, False) - will be split further
    "kd": "kw",  # Keyword.Declaration -> keyword
    "kn": "kw",  # Keyword.Namespace -> keyword
    "kr": "kw",  # Keyword.Reserved -> keyword
    "o": "op",  # Operator -> operator
    "ow": "op",  # Operator.Word -> operator
    "p": "",  # Punctuation -> no special class
    "s": "st",  # String -> string
    "s1": "st",  # String.Single -> string
    "s2": "st",  # String.Double -> string
    "mi": "dv",  # Number.Integer -> decimal value
    "mf": "fl",  # Number.Float -> float
    "c": "co",  # Comment -> comment
    "c1": "co",  # Comment.Single -> comment
}


def highlight_signature_with_pygments(html_content):
    """
    Re-highlight the main signature block (cb1) with Pygments for better syntax coloring.

    This extracts the signature code, highlights it with Pygments, then maps the Pygments CSS
    classes to Quarto's highlighting classes for consistency.
    """
    # Find the main signature code block: the first code block on the page
    # (id="cb1") *and* the one the renderer wrapped in a doc-signature div.
    # The enclosing div is what makes this a signature; without it the first
    # code block on a page is whatever ordinary block comes first, which on a
    # page whose signature is inline markup is usually an Examples doctest.
    cb1_pattern = re.compile(
        r'(<div class="doc-signature[^"]*">\s*'
        r'<div class="sourceCode" id="cb1">.*?<code class="sourceCode python">)'
        r"(.*?)"
        r"(</code>.*?</div>)",
        re.DOTALL,
    )

    def replace_signature(match):
        prefix = match.group(1)
        code_content = match.group(2)
        suffix = match.group(3)

        # Extract plain text from the HTML spans
        # Remove HTML tags but preserve the text content
        plain_code = re.sub(r"<[^>]+>", "", code_content)
        # Clean up the text (unescape HTML entities)
        plain_code = plain_code.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")

        # Highlight with Pygments
        lexer = PythonLexer()
        # Use a custom formatter that generates short class names
        formatter = HtmlFormatter(nowrap=True, classprefix="")

        highlighted = highlight(plain_code, lexer, formatter)

        # Map Pygments classes to Quarto classes
        for pg_class, quarto_class in PYGMENTS_TO_QUARTO_CLASS.items():
            if quarto_class:
                highlighted = highlighted.replace(f'class="{pg_class}"', f'class="{quarto_class}"')
            else:
                # Remove empty class attributes
                highlighted = re.sub(
                    rf'<span class="{pg_class}">([^<]*)</span>', r"\1", highlighted
                )

        # Special handling: make method/function name stand out on every signature line
        # Pattern: ClassName.method_name( or function_name( — or, for a TypedDict/Enum
        # page, just Name with nothing after it, since those never show call brackets.
        # The trailing group matches either the opening "(" of a call or the end of the
        # line, so a bare name is still caught without also matching a name that is
        # followed by anything else (which never happens: this block only ever holds a
        # signature line, and a signature line's leading name is always either called,
        # in which case "(" comes right after it, or not, in which case nothing does).
        # Uses re.MULTILINE so ^ matches each line (important for @overload signatures)
        sig_name_pattern = re.compile(
            r'^(<span class="va">)(\w+)(</span>)(<span class="op">\.</span>)?'
            r'(<span class="va">)?(\w+)?(</span>)?(\(|$)',
            re.MULTILINE,
        )

        def enhance_sig_name(m):
            # The final group is "(" for a call, or "" at the end of a bracket-less
            # TypedDict/Enum line; either way it belongs straight after the name.
            trailing = m.group(8)
            # If there's a dot, it's ClassName.method_name
            if m.group(4):  # Has dot
                class_name = m.group(2)
                method_name = m.group(6) or ""
                return (
                    f'<span class="sig-class">{class_name}</span>'
                    f'<span class="op">.</span>'
                    f'<span class="sig-name">{method_name}</span>{trailing}'
                )
            else:
                # Just function_name( or, bracket-less, just Name
                func_name = m.group(2)
                return f'<span class="sig-name">{func_name}</span>{trailing}'

        highlighted = sig_name_pattern.sub(enhance_sig_name, highlighted)

        # Differentiate None from True/False
        # None gets 'cn-none' class, True/False get 'cn-bool' class
        highlighted = highlighted.replace(
            '<span class="cn">None</span>', '<span class="cn-none">None</span>'
        )
        highlighted = highlighted.replace(
            '<span class="cn">True</span>', '<span class="cn-bool">True</span>'
        )
        highlighted = highlighted.replace(
            '<span class="cn">False</span>', '<span class="cn-bool">False</span>'
        )

        # Convert single quotes to double quotes in string literals
        # Pygments outputs HTML entities: &#39; for single quote
        # Match both s1 (string single) and st (after class mapping) classes
        highlighted = re.sub(
            r'<span class="(st|s1)">&#39;([^&]*)&#39;</span>',
            r'<span class="\1">&quot;\2&quot;</span>',
            highlighted,
        )

        # Wrap each line in a span with proper id for line linking
        # For overloaded functions (multiple signature lines), insert a blank spacer
        # span between each signature for visual separation.
        lines = highlighted.split("\n")
        # Filter out empty trailing lines
        while lines and not lines[-1].strip():
            lines.pop()
        is_overloaded = len(lines) > 1 and all(line.strip() == "" or "(" in line for line in lines)
        wrapped_lines = []
        line_num = 1
        for idx, line in enumerate(lines):
            if not line and not is_overloaded:
                continue  # Skip empty lines for non-overloaded functions
            if not line:
                continue  # Skip blank lines; we insert spacers ourselves
            wrapped_lines.append(
                f'<span id="cb1-{line_num}"><a href="#cb1-{line_num}" aria-hidden="true" tabindex="-1"></a>{line}</span>'
            )
            line_num += 1
            # Add a blank spacer line between overload signatures (not after the last)
            if is_overloaded and idx < len(lines) - 1:
                wrapped_lines.append(f'<span id="cb1-{line_num}" class="overload-spacer"> </span>')
                line_num += 1

        new_code = "\n".join(wrapped_lines)

        return f"{prefix}{new_code}{suffix}"

    return cb1_pattern.sub(replace_signature, html_content)


# Process all HTML files in the `_site/reference/` directory (except `index.html`)
# and apply the specified transformations
html_files = [f for f in glob.glob("_site/reference/*.html") if os.path.basename(f) != "index.html"]

print(f"Found {len(html_files)} HTML files to process")

for html_file in html_files:
    print(f"Processing: {html_file}")

    # Extract the item name from the filename (e.g., "GreatDocs.html" -> "GreatDocs")
    item_name_from_file = os.path.basename(html_file).replace(".html", "")

    with open(html_file, "r", encoding="utf-8") as file:
        content = file.read()

    # Re-highlight the signature with Pygments for better syntax coloring
    content = highlight_signature_with_pygments(content)

    # Convert back to lines for line-by-line processing
    content = content.splitlines(keepends=True)

    content_str = "".join(content)

    # Inject constant value/annotation into constant reference pages.
    # Replaces the bare ``<p><code>NAME</code></p>`` that the renderer emits with a
    # styled display showing the type annotation and assigned value.
    obj_type_for_value = object_types.get(item_name_from_file)
    if obj_type_for_value == "constant" and item_name_from_file in constant_values:
        meta = constant_values[item_name_from_file]
        annotation = meta.get("annotation", "")
        value = meta.get("value", "")

        # Build the display string:  NAME: type = value
        parts = [f"<code>{item_name_from_file}</code>"]
        if annotation:
            parts.append(f'<code style="color: #6b7280;">: {annotation}</code>')
        if value:
            parts.append(f'<code style="color: #6b7280;"> = {value}</code>')
        replacement_html = "<p>" + "".join(parts) + "</p>"

        # Replace the original bare name paragraph
        bare_name_html = f"<p><code>{item_name_from_file}</code></p>"
        if bare_name_html in content_str:
            content_str = content_str.replace(bare_name_html, replacement_html, 1)

    content = content_str.splitlines(keepends=True)

    # Add separator lines between class details and individual members,
    # and between individual member sections.
    # - Thin solid line after the Methods/Attributes summary table (before first member section)
    # - Dotted line between each individual member section
    for i, line in enumerate(content):
        # Individual members render as `level3` sections.
        if "<section id=" in line and 'class="level3"' in line:
            # Check if the previous non-blank line ends a table (</table> in </section>)
            # or closes another member section
            for j in range(i - 1, max(0, i - 5), -1):
                prev = content[j].strip()
                if not prev:
                    continue
                if "</table>" in prev:
                    # First member section after a summary table — solid line
                    content[i] = (
                        '<hr style="border: none; border-top: 2px solid #c5c8cd; margin: 1.5rem 0 1rem 0;">\n'
                        + content[i]
                    )
                elif "</section>" in prev:
                    # Between member sections — dotted line
                    content[i] = (
                        '<hr style="border: none; border-top: 2px dotted #b0b4ba; margin: 1.5rem 0 1rem 0;">\n'
                        + content[i]
                    )
                break

    with open(html_file, "w", encoding="utf-8") as file:
        file.writelines(content)


all_html_files = glob.glob("_site/**/*.html", recursive=True)
print(f"Found {len(all_html_files)} HTML files to check for the search title")

for html_file in all_html_files:
    with open(html_file, "r", encoding="utf-8") as file:
        content = file.read()

    # Remove the title attribute from #quarto-search to prevent a duplicate
    # tooltip: Quarto sets title="<search-label>" on the parent div, but the
    # autocomplete library also creates a child button with its own title.
    # Stripping the parent avoids two tooltips stacking on hover.
    _search_title_pat = r'(<div\s+id="quarto-search"\s+class="[^"]*")\s+title="[^"]*"'
    new_content = re.sub(_search_title_pat, r"\1", content)
    if new_content != content:
        with open(html_file, "w", encoding="utf-8") as file:
            file.write(new_content)

print("Finished processing all files")
print("##GD:PASS:Reference pages processed", flush=True)


# ── Translate autocomplete search-button title ──────────────────────────────
# The Algolia autocomplete library (autocomplete.umd.js) ships with a
# hardcoded default  detachedSearchButtonTitle:"Search".  Quarto's search JS
# passes  clearButtonTitle / submitButtonTitle / detachedCancelButtonText  to
# the library but omits  detachedSearchButtonTitle, so the button always
# shows "Search".  If the site language is not English, patch the bundled JS
# to use the correct search-label translation instead.
_autocomplete_js = glob.glob("_site/**/autocomplete.umd.js", recursive=True)
if _autocomplete_js:
    _search_label = None
    # Read the search-label from any HTML page's search-options JSON
    for _hf in all_html_files[:5]:
        with open(_hf, "r", encoding="utf-8") as f:
            _hcontent = f.read()
        _sl_m = re.search(r'"search-label"\s*:\s*"([^"]+)"', _hcontent)
        if _sl_m:
            _search_label = _sl_m.group(1)
            break
    if _search_label and _search_label != "Search":
        for _acjs in _autocomplete_js:
            with open(_acjs, "r", encoding="utf-8") as f:
                _ac_content = f.read()
            _old = 'detachedSearchButtonTitle:"Search"'
            if _old in _ac_content:
                _ac_content = _ac_content.replace(
                    _old, f'detachedSearchButtonTitle:"{_search_label}"'
                )
                with open(_acjs, "w", encoding="utf-8") as f:
                    f.write(_ac_content)
                print(f"Patched search button title to '{_search_label}' in {_acjs}")


# ============================================================================
# GitHub Widget Injection
# ============================================================================
# Replace escaped GitHub widget placeholder with actual HTML
# This handles cases where Quarto escapes the HTML in navbar text items


def inject_github_widget():
    """
    Find and replace escaped GitHub widget placeholders with actual widget HTML.

    Quarto escapes HTML in navbar text items, so we need to post-process to inject the actual widget
    div.
    """
    print("Checking for GitHub widget placeholders...")

    widget_escaped_pattern = re.compile(
        r'<span class="menu-text">&lt;div id="github-widget" '
        r'data-owner="([^"]*)" data-repo="([^"]*)"'
        r'((?:\s+data-[a-z]+=(?:"[^"]*"|&quot;[^&]*&quot;))*)'
        r"&gt;&lt;/div&gt;</span>"
    )

    widget_count = 0

    for html_file in all_html_files:
        with open(html_file, "r", encoding="utf-8") as file:
            content = file.read()

        # Check if this file has an escaped widget placeholder
        match = widget_escaped_pattern.search(content)
        if match:
            owner = match.group(1)
            repo = match.group(2)
            extra_attrs_raw = match.group(3)

            # Unescape any &quot; in extra attributes
            extra_attrs = extra_attrs_raw.replace("&quot;", '"') if extra_attrs_raw else ""

            # Replace with actual widget HTML
            replacement = f'<div id="github-widget" data-owner="{owner}" data-repo="{repo}"{extra_attrs}></div>'
            content = widget_escaped_pattern.sub(replacement, content)

            with open(html_file, "w", encoding="utf-8") as file:
                file.write(content)

            widget_count += 1

    if widget_count > 0:
        print(f"Injected GitHub widget into {widget_count} HTML files")
    else:
        print("No GitHub widget placeholders found")


inject_github_widget()
print("##GD:PASS:GitHub widget injected", flush=True)


# ============================================================================
# Version Badge Injection
# ============================================================================
# Insert a version badge into the navbar title from _package_meta.json


def inject_version_badge():
    """
    Inject a version badge next to the package name in the navbar.

    Reads package version (and optional release date) from `_package_meta.json` (written by the
    build) and inserts a small badge span inside each `<span class="navbar-title">` element across
    all rendered HTML files.  When a `published_at` date is present the badge receives a `title`
    attribute so the release date appears as a native browser tooltip on hover.
    """
    meta_path = "_package_meta.json"
    if not os.path.exists(meta_path):
        print("No _package_meta.json found, skipping version badge injection")
        return

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    version = meta.get("version", "")
    if not version:
        print("No version in _package_meta.json, skipping version badge injection")
        return

    # Build an optional title attribute with the release date
    published_at = meta.get("published_at", "")
    title_attr = ""
    if published_at:
        # published_at is ISO-8601 e.g. "2025-06-15T12:00:00Z"
        date_str = published_at[:10]  # "2025-06-15"
        title_attr = f' title="Released {date_str}"'

    print(f"Injecting version badge v{version} into navbar...")

    # Match <span class="navbar-title">PackageName</span>
    navbar_title_pattern = re.compile(r'(<span class="navbar-title">)(.*?)(</span>)')

    badge_html = f'<span class="version-badge"{title_attr}>v{version}</span>'

    # Build a Tippy.js tooltip for the logo (used when navbar has a logo
    # instead of a text title).  The tooltip shows version tag + release date.
    # Uses HTML content: <code> for the version, <br> for the line break.
    import html as _html_esc

    version_html = f"<code>v{_html_esc.escape(version)}</code>"
    if published_at:
        date_str = published_at[:10]
        released_line = _t("logo_tooltip_released", "Released {date}").replace(
            "{date}", _html_esc.escape(date_str)
        )
        logo_tooltip_html = f"{version_html}<br>{_html_esc.escape(released_line)}"
    else:
        logo_tooltip_html = version_html

    # Pattern to match the <a> navbar-brand-logo link wrapping logo images
    logo_brand_pattern = re.compile(r'(<a\b[^>]*class="navbar-brand navbar-brand-logo"[^>]*)>')

    badge_count = 0
    logo_count = 0

    for html_file in all_html_files:
        with open(html_file, "r", encoding="utf-8") as file:
            content = file.read()

        modified = False

        match = navbar_title_pattern.search(content)
        if match:
            # Only inject if badge not already present
            if "version-badge" not in content:
                replacement = f"{match.group(1)}{match.group(2)} {badge_html}{match.group(3)}"
                content = navbar_title_pattern.sub(replacement, content)
                modified = True
                badge_count += 1

        # When there's a logo but no text title, add a tooltip to the logo link
        if not match:
            logo_match = logo_brand_pattern.search(content)
            if logo_match and 'data-tippy-content="' not in logo_match.group(0):
                escaped_tooltip = logo_tooltip_html.replace('"', "&quot;")
                replacement = f'{logo_match.group(1)} data-tippy-content="{escaped_tooltip}">'
                content = logo_brand_pattern.sub(replacement, content, count=1)
                modified = True
                logo_count += 1

        if modified:
            with open(html_file, "w", encoding="utf-8") as file:
                file.write(content)

    if badge_count > 0:
        print(f"Injected version badge into {badge_count} HTML files")
    if logo_count > 0:
        print(f"Added version tooltip to navbar logo in {logo_count} HTML files")
    if badge_count == 0 and logo_count == 0:
        print("No navbar titles or logos found for version badge injection")


inject_version_badge()
print("##GD:PASS:Version badge injected", flush=True)


def disable_sidebar_collapse():
    """
    Strip Bootstrap collapse attributes from sidebar section toggles and ensure all sidebar sections
    remain permanently expanded.

    Removes data-bs-toggle, data-bs-target, aria-expanded, and role attributes from sidebar collapse
    triggers. Also removes the collapse class from sidebar section `<ul>` elements and removes the
    chevron toggle `<a>` elements entirely.
    """
    html_files = glob.glob("_site/**/*.html", recursive=True)
    modified_count = 0

    for html_file in html_files:
        with open(html_file, "r", encoding="utf-8") as f:
            content = f.read()

        original = content

        # Remove the chevron toggle <a> elements entirely
        content = re.sub(
            r'\s*<a class="sidebar-item-toggle text-start"[^>]*>.*?</a>\s*',
            "\n",
            content,
            flags=re.DOTALL,
        )

        # Strip data-bs-toggle="collapse" and data-bs-target from section heading links
        content = re.sub(
            r'(<a class="sidebar-item-text sidebar-link text-start")'
            r'\s+data-bs-toggle="collapse"'
            r'\s+data-bs-target="#[^"]*"'
            r'\s+role="navigation"'
            r'\s+aria-expanded="[^"]*"',
            r"\1",
            content,
        )

        # Remove 'collapse' class from sidebar section <ul> elements
        # e.g. class="collapse list-unstyled sidebar-section depth1 show"
        # becomes class="list-unstyled sidebar-section depth1 show"
        content = re.sub(
            r'(<ul id="quarto-sidebar-section-\d+" class=")collapse\s+',
            r"\1",
            content,
        )

        if content != original:
            with open(html_file, "w", encoding="utf-8") as f:
                f.write(content)
            modified_count += 1

    print(f"Disabled sidebar collapse in {modified_count} HTML files")


disable_sidebar_collapse()
print("##GD:PASS:Sidebar collapse disabled", flush=True)


def remove_empty_footer_divs():
    """
    Remove empty nav-footer divs that contain only whitespace or &nbsp;.

    Quarto always renders all three footer sections (left, center, right) even when only one has
    content. The empty divs cause excess vertical spacing on mobile viewports due to flex-wrap
    margins.
    """
    html_files = glob.glob("_site/**/*.html", recursive=True)
    modified_count = 0

    empty_div_pattern = re.compile(
        r'\s*<div class="nav-footer-(left|center|right)">\s*'
        r"(?:&nbsp;|\s)*"
        r"</div>\s*",
        re.DOTALL,
    )

    for html_file in html_files:
        with open(html_file, "r", encoding="utf-8") as f:
            content = f.read()

        original = content
        content = empty_div_pattern.sub("\n", content)

        if content != original:
            with open(html_file, "w", encoding="utf-8") as f:
                f.write(content)
            modified_count += 1

    print(f"Removed empty footer divs from {modified_count} HTML files")


remove_empty_footer_divs()
print("##GD:PASS:Empty footer divs removed", flush=True)


def fix_script_paths():
    """
    Fix relative script paths for HTML files in subdirectories.

    Quarto's include-after-body with text doesn't resolve paths relative to the output file's
    location. This function finds script tags with relative paths and adjusts them based on the
    file's depth in the directory structure.
    """
    print("Fixing script paths for subdirectory pages...")

    fixed_count = 0

    for html_file in all_html_files:
        # Calculate depth relative to _site directory
        rel_path = os.path.relpath(html_file, "_site")
        depth = rel_path.count(os.sep)

        # Skip files at root level (depth 0)
        if depth == 0:
            continue

        with open(html_file, "r", encoding="utf-8") as file:
            content = file.read()

        # Build the relative path prefix (e.g., "../" for depth 1, "../../" for depth 2)
        prefix = "../" * depth

        modified = False

        # Fix github-widget.js path
        old_gh_script = '<script src="github-widget.js"></script>'
        new_gh_script = f'<script src="{prefix}github-widget.js"></script>'

        if old_gh_script in content:
            content = content.replace(old_gh_script, new_gh_script)
            modified = True

        # Fix sidebar-filter.js path
        old_filter_script = '<script src="sidebar-filter.js"></script>'
        new_filter_script = f'<script src="{prefix}sidebar-filter.js"></script>'

        if old_filter_script in content:
            content = content.replace(old_filter_script, new_filter_script)
            modified = True

        # Fix sidebar-wrap.js path
        old_wrap_script = '<script src="sidebar-wrap.js"></script>'
        new_wrap_script = f'<script src="{prefix}sidebar-wrap.js"></script>'

        if old_wrap_script in content:
            content = content.replace(old_wrap_script, new_wrap_script)
            modified = True

        # Fix reference-switcher.js path
        old_ref_switcher = '<script src="reference-switcher.js"></script>'
        new_ref_switcher = f'<script src="{prefix}reference-switcher.js"></script>'

        if old_ref_switcher in content:
            content = content.replace(old_ref_switcher, new_ref_switcher)
            modified = True

        # Fix version-selector.js path
        old_version_sel = '<script src="version-selector.js"></script>'
        new_version_sel = f'<script src="{prefix}version-selector.js"></script>'

        if old_version_sel in content:
            content = content.replace(old_version_sel, new_version_sel)
            modified = True

        # Fix dark-mode-toggle.js path
        old_dark_mode = '<script src="dark-mode-toggle.js"></script>'
        new_dark_mode = f'<script src="{prefix}dark-mode-toggle.js"></script>'

        if old_dark_mode in content:
            content = content.replace(old_dark_mode, new_dark_mode)
            modified = True

        # Fix theme-init.js path
        old_theme_init = '<script src="theme-init.js"></script>'
        new_theme_init = f'<script src="{prefix}theme-init.js"></script>'

        if old_theme_init in content:
            content = content.replace(old_theme_init, new_theme_init)
            modified = True

        # Fix copy-page.js path
        old_copy_page = '<script src="copy-page.js"></script>'
        new_copy_page = f'<script src="{prefix}copy-page.js"></script>'

        if old_copy_page in content:
            content = content.replace(old_copy_page, new_copy_page)
            modified = True

        # Fix announcement-banner.js path
        old_ann_banner = '<script src="announcement-banner.js"></script>'
        new_ann_banner = f'<script src="{prefix}announcement-banner.js"></script>'

        if old_ann_banner in content:
            content = content.replace(old_ann_banner, new_ann_banner)
            modified = True

        # Fix navbar-style.js path
        old_nb_style = '<script src="navbar-style.js"></script>'
        new_nb_style = f'<script src="{prefix}navbar-style.js"></script>'

        if old_nb_style in content:
            content = content.replace(old_nb_style, new_nb_style)
            modified = True

        # Fix content-style.js path
        old_cs_style = '<script src="content-style.js"></script>'
        new_cs_style = f'<script src="{prefix}content-style.js"></script>'

        if old_cs_style in content:
            content = content.replace(old_cs_style, new_cs_style)
            modified = True

        # Fix copy-code.js path
        old_copy_code = '<script src="copy-code.js"></script>'
        new_copy_code = f'<script src="{prefix}copy-code.js"></script>'

        if old_copy_code in content:
            content = content.replace(old_copy_code, new_copy_code)
            modified = True

        # Fix page-metadata.js path
        old_page_meta = '<script src="page-metadata.js"></script>'
        new_page_meta = f'<script src="{prefix}page-metadata.js"></script>'

        if old_page_meta in content:
            content = content.replace(old_page_meta, new_page_meta)
            modified = True

        # Fix tooltips.js path
        old_tooltips = '<script src="tooltips.js"></script>'
        new_tooltips = f'<script src="{prefix}tooltips.js"></script>'

        if old_tooltips in content:
            content = content.replace(old_tooltips, new_tooltips)
            modified = True

        # Fix responsive-tables.js path
        old_resp_tables = '<script src="responsive-tables.js"></script>'
        new_resp_tables = f'<script src="{prefix}responsive-tables.js"></script>'

        if old_resp_tables in content:
            content = content.replace(old_resp_tables, new_resp_tables)
            modified = True

        # Fix back-to-top.js path
        old_back_to_top = '<script src="back-to-top.js"></script>'
        new_back_to_top = f'<script src="{prefix}back-to-top.js"></script>'

        if old_back_to_top in content:
            content = content.replace(old_back_to_top, new_back_to_top)
            modified = True

        # Fix keyboard-nav.js path
        old_keyboard_nav = '<script src="keyboard-nav.js"></script>'
        new_keyboard_nav = f'<script src="{prefix}keyboard-nav.js"></script>'

        if old_keyboard_nav in content:
            content = content.replace(old_keyboard_nav, new_keyboard_nav)
            modified = True

        # Fix navbar-widgets.js path
        old_navbar_widgets = '<script src="navbar-widgets.js"></script>'
        new_navbar_widgets = f'<script src="{prefix}navbar-widgets.js"></script>'

        if old_navbar_widgets in content:
            content = content.replace(old_navbar_widgets, new_navbar_widgets)
            modified = True

        # Fix mermaid-renderer.js path
        old_mermaid = '<script src="mermaid-renderer.js"></script>'
        new_mermaid = f'<script src="{prefix}mermaid-renderer.js"></script>'

        if old_mermaid in content:
            content = content.replace(old_mermaid, new_mermaid)
            modified = True

        # Fix details.js path
        old_details = '<script src="details.js"></script>'
        new_details = f'<script src="{prefix}details.js"></script>'

        if old_details in content:
            content = content.replace(old_details, new_details)
            modified = True

        # Fix page-tags.js path
        old_page_tags = '<script src="page-tags.js"></script>'
        new_page_tags = f'<script src="{prefix}page-tags.js"></script>'

        if old_page_tags in content:
            content = content.replace(old_page_tags, new_page_tags)
            modified = True

        if modified:
            with open(html_file, "w", encoding="utf-8") as file:
                file.write(content)
            fixed_count += 1

    if fixed_count > 0:
        print(f"Fixed script paths in {fixed_count} HTML files in subdirectories")
    else:
        print("No script path fixes needed")


fix_script_paths()
print("##GD:PASS:Script paths fixed", flush=True)


# Remove copy-page widget from the root index page (homepage) and the
# reference index page.  Neither is a regular documentation page so the
# Copy / View-as-Markdown buttons are not useful.
for _idx_label, _idx_path in [
    ("homepage", os.path.join("_site", "index.html")),
    ("reference index", os.path.join("_site", "reference", "index.html")),
    ("CLI reference index", os.path.join("_site", "reference", "cli", "index.html")),
]:
    if os.path.isfile(_idx_path):
        with open(_idx_path, "r", encoding="utf-8") as f:
            _idx_html = f.read()
        _idx_cleaned = re.sub(
            r'<script src="([./ ]*?)copy-page\.js"></script>\n?',
            "",
            _idx_html,
        )
        if _idx_cleaned != _idx_html:
            with open(_idx_path, "w", encoding="utf-8") as f:
                f.write(_idx_cleaned)
            print(f"Removed copy-page widget from {_idx_label}")


def inject_sidebar_body_classes():
    """
    Inject a `gd-ref-sidebar` class on the `<body>` tag for API/CLI reference pages.

    This allows CSS to scope monospace sidebar fonts to reference pages while keeping the default
    sans-serif font for user-guide, recipe, and other pages.
    """
    print("Injecting sidebar body classes...")
    count = 0

    for html_file in all_html_files:
        rel_path = os.path.relpath(html_file, "_site")
        # Match pages under reference/ (covers both API and CLI reference)
        if not rel_path.startswith("reference" + os.sep) and rel_path != "reference":
            continue

        with open(html_file, "r", encoding="utf-8") as f:
            content = f.read()

        new_content = content.replace('<body class="', '<body class="gd-ref-sidebar ', 1)
        if new_content != content:
            with open(html_file, "w", encoding="utf-8") as f:
                f.write(new_content)
            count += 1

    print(f"Injected gd-ref-sidebar class in {count} reference HTML files")


inject_sidebar_body_classes()
print("##GD:PASS:Sidebar body classes injected", flush=True)


def style_api_index_sidebar_item():
    """
    Apply inline styles to the 'API Index' / 'CLI Index' sidebar links so they visually separate
    from the monospace reference entries.

    Targets the `<a>` whose href ends with 'reference/index.html' (API index) or
    'reference/cli/index.html' (CLI index) and its parent `<div class="sidebar-item-container">`.
    """
    import re

    print("Styling reference index sidebar items...")
    count = 0
    font = (
        "&quot;Open Sans&quot;, -apple-system, BlinkMacSystemFont, "
        "&quot;Segoe UI&quot;, Roboto, &quot;Helvetica Neue&quot;, Arial, sans-serif"
    )
    # Match the API index link, or the CLI index link (the extra '/cli' segment is optional).
    href_pat = r'href="[^"]*reference/(?:cli/)?index\.html"'

    for html_file in all_html_files:
        rel_path = os.path.relpath(html_file, "_site")
        if not rel_path.startswith("reference" + os.sep) and rel_path != "reference":
            continue

        with open(html_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Find the sidebar-item-container div immediately followed by the index link.
        match = re.search(
            r'(<div class="sidebar-item-container")(>\s*<a )([^>]*' + href_pat + r"[^>]*>)",
            content,
        )
        if not match:
            continue

        div_open = match.group(1)
        between = match.group(2)
        a_tag = match.group(3)

        styled_div = div_open + ' style="padding-bottom: 0.5rem; padding-top: 0.25rem;"'
        styled_a = a_tag.replace(
            'class="',
            f'style="font-family: {font};" class="',
            1,
        )

        new_content = content.replace(
            match.group(0),
            styled_div + between + styled_a,
        )

        if new_content != content:
            with open(html_file, "w", encoding="utf-8") as f:
                f.write(new_content)
            count += 1

    print(f"Styled reference index sidebar items in {count} reference HTML files")


style_api_index_sidebar_item()
print("##GD:PASS:API Index sidebar styled", flush=True)


# ============================================================================
# Generate Markdown (.md) versions of each page
# ============================================================================
# Uses Quarto's bundled pandoc to convert the main content area of each
# rendered HTML page to GitHub-Flavored Markdown.  The resulting .md files
# live alongside the .html files in _site/ and are used by the copy-page
# widget (copy to clipboard / view as plain Markdown).


def _postprocess_markdown_content(md_content: str, rel: str) -> str:
    """Apply markdown cleanup and link normalization after pandoc conversion."""

    # Decode HTML entities that may have been double-encoded or improperly handled.
    # This fixes issues like: &rsquo; -> ', &ldquo; -> ", etc.
    md_content = html.unescape(md_content)

    # Fix common UTF-8 mojibake sequences (UTF-8 bytes interpreted as Latin-1).
    # When UTF-8 bytes are incorrectly decoded as Latin-1, we get mojibake patterns.
    # E.g., U+2019 (') is bytes E2 80 99 in UTF-8, but decoded as Latin-1 becomes
    # the three characters U+00E2 U+20AC U+2122, which looks like: â€™
    mojibake_fixes = {
        "\u00e2\u20ac\u2122": "\u2019",  # Right single quotation mark
        "\u00e2\u20ac\u009c": "\u201c",  # Left double quotation mark
        "\u00e2\u20ac\u009d": "\u201d",  # Right double quotation mark
        "\u00e2\u20ac\u0093": "\u2013",  # En dash
        "\u00e2\u20ac\u0094": "\u2014",  # Em dash
        # Alternate mojibake form with C1 control characters.
        "\u00e2\u0080\u0099": "\u2019",
        "\u00e2\u0080\u009c": "\u201c",
        "\u00e2\u0080\u009d": "\u201d",
        "\u00e2\u0080\u0093": "\u2013",
        "\u00e2\u0080\u0094": "\u2014",
    }
    for mojibake, correct in mojibake_fixes.items():
        md_content = md_content.replace(mojibake, correct)

    # Normalize typography to plain ASCII for robust display/copy across
    # environments that may not preserve UTF-8 metadata for raw .md files.
    typography_fixes = {
        "\u2018": "'",  # Left single quotation mark
        "\u2019": "'",  # Right single quotation mark
        "\u201c": '"',  # Left double quotation mark
        "\u201d": '"',  # Right double quotation mark
        "\u2013": "-",  # En dash
        "\u2014": "--",  # Em dash
    }
    for src, dst in typography_fixes.items():
        md_content = md_content.replace(src, dst)

    # Remove standalone Source links (HTML and markdown forms).
    md_content = re.sub(
        r"^\s*<a\s+[^>]*>\s*source\s*</a>\s*\n?",
        "",
        md_content,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    md_content = re.sub(
        r"^\s*\[source\]\([^\n)]+\)\s*\n?",
        "",
        md_content,
        flags=re.IGNORECASE | re.MULTILINE,
    )

    # Convert simple HTML anchors to markdown links.
    # Keep this conservative: only convert when anchor text has no nested tags.
    def _anchor_to_md(m):
        href = m.group("href").strip()
        text = m.group("text")
        if "<" in text or ">" in text:
            return m.group(0)
        text = html.unescape(text).strip()
        if not text:
            return m.group(0)
        return f"[{text}]({href})"

    md_content = re.sub(
        r'<a\s+[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<text>.*?)</a>',
        _anchor_to_md,
        md_content,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Normalize parameter signature artifacts created by adjacent code spans.
    # These can appear in various formats depending on how pandoc combined the spans.
    # Pattern 1: `name``:`` ``type`` ``=`` ``default`` - full three-part with optional closing backtick
    md_content = re.sub(
        r"`([^`\n]+)``:``\s*``([^`\n]+)``\s*``=``\s*``([^`]+?)``?",
        r"`\1`: `\2` = `\3`",
        md_content,
    )
    # Pattern 2: `name``:`` ``type`` - just name and type
    md_content = re.sub(
        r"`([^`\n]+)``:``\s*``([^`\n]+)``",
        r"`\1`: `\2`",
        md_content,
    )

    # Remove leftover HTML div wrappers that pandoc preserved.
    md_content = re.sub(
        r"^<div[^>]*>\s*$",
        "",
        md_content,
        flags=re.MULTILINE,
    )
    md_content = re.sub(
        r"^</div>\s*$",
        "",
        md_content,
        flags=re.MULTILINE,
    )

    # Remove leftover <span> tags with parameter/annotation classes.
    md_content = re.sub(
        r'<span\s+class="parameter-[^"]*"[^>]*>(.*?)</span>',
        r"\1",
        md_content,
    )

    # Rewrite internal .html links to .md (relative paths only).
    md_content = re.sub(
        r"\]\((\.\./[^)]*?)\.html(\)?)",
        r"](\1.md\2",
        md_content,
    )
    # Also in the same directory.
    md_content = re.sub(
        r"\]\(([A-Za-z0-9_][^):/]*?)\.html(\)?)",
        r"](\1.md\2",
        md_content,
    )

    # Simplify redundant ../current_dir/ paths to ./
    file_dir = os.path.dirname(rel)
    if file_dir:
        escaped = re.escape("../" + file_dir + "/")
        md_content = re.sub(
            r"\]\(" + escaped + r"([^)]+)\)",
            r"](\1)",
            md_content,
        )

    # Remove leftover <span> tags (screen-reader, callout-icon, etc.)
    md_content = re.sub(
        r'<span\s+class="[^"]*">(.*?)</span>',
        r"\1",
        md_content,
    )
    # Remove empty <i> tags (callout icons)
    md_content = re.sub(r"<i[^>]*></i>", "", md_content)

    # Clean up excessive blank lines (3+ → 2)
    md_content = re.sub(r"\n{4,}", "\n\n\n", md_content)

    # Strip trailing whitespace
    md_content = md_content.strip() + "\n"

    return md_content


def _prepare_html_for_pandoc(html_file: str) -> tuple[str, str, str | None]:
    """Extract and clean main content from an HTML file for pandoc conversion.

    Returns (html_file, rel_path, cleaned_html_or_None).
    """
    rel = os.path.relpath(html_file, "_site")

    if not html_file.endswith(".html"):
        return html_file, rel, None

    try:
        with open(html_file, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, ValueError):
        return html_file, rel, None

    main_match = re.search(
        r'<main\s+class="content"[^>]*>(.*?)</main>',
        content,
        re.DOTALL,
    )
    if not main_match:
        return html_file, rel, None

    main_html = main_match.group(1)

    main_html = re.sub(
        r'<nav\s+class="page-navigation".*?</nav>',
        "",
        main_html,
        flags=re.DOTALL,
    )
    main_html = re.sub(
        r'<div\s+class="quarto-title-meta">.*?</div>\s*',
        "",
        main_html,
        flags=re.DOTALL,
    )

    title_match = re.search(
        r'<header[^>]*class="quarto-title-block[^"]*"[^>]*>'
        r'.*?<div\s+class="quarto-title">\s*'
        r"(<h[12][^>]*>)(.*?)(</h[12]>)"
        r".*?</header>",
        main_html,
        re.DOTALL,
    )
    if title_match:
        heading_tag_open = title_match.group(1)
        heading_inner = title_match.group(2)
        heading_tag_close = title_match.group(3)
        heading_inner = re.sub(
            r'<span\s+style="[^"]*border-style:\s*solid[^"]*">.*?</span>',
            "",
            heading_inner,
            flags=re.DOTALL,
        )
        clean_title = f"{heading_tag_open}{heading_inner.strip()}{heading_tag_close}"
        main_html = main_html[: title_match.start()] + clean_title + main_html[title_match.end() :]

    main_html = re.sub(
        r'<div\s+class="code-with-filename">\s*'
        r'<div\s+class="code-with-filename-file">\s*<pre><strong>(.*?)</strong></pre>\s*</div>\s*'
        r'<div\s+class="code-copy-outer-scaffold">\s*',
        r"<p><strong>\1</strong></p>\n",
        main_html,
        flags=re.DOTALL,
    )

    main_html = re.sub(
        r'<div\s+class="code-copy-outer-scaffold">\s*',
        "",
        main_html,
    )
    main_html = re.sub(
        r'<nav\s+class="gd-code-nav">.*?</nav>',
        "",
        main_html,
        flags=re.DOTALL,
    )
    main_html = re.sub(
        r'<button\s+title="Copy to [Cc]lipboard"[^>]*>.*?</button>',
        "",
        main_html,
        flags=re.DOTALL,
    )

    main_html = re.sub(
        r'<div\s+class="usage-source-row"[^>]*>.*?</div>',
        "",
        main_html,
        flags=re.DOTALL,
    )

    main_html = re.sub(
        r'<a[^>]*class="source-link"[^>]*>.*?</a>',
        "",
        main_html,
        flags=re.DOTALL,
    )

    def _rewrite_code_block(m):
        full = m.group(0)
        lang_m = re.search(r'<pre\s+class="sourceCode\s+(\w+)', full)
        lang = lang_m.group(1) if lang_m else ""
        code_m = re.search(r"(<code[^>]*>)(.*?)(</code>)", full, re.DOTALL)
        if not code_m:
            return full
        code_content = code_m.group(2)
        if lang:
            return f'<pre><code class="language-{lang}">{code_content}</code></pre>'
        return f"<pre><code>{code_content}</code></pre>"

    main_html = re.sub(
        r'<div\s+[^>]*class="sourceCode[^"]*"[^>]*>\s*<pre[^>]*>.*?</pre>\s*</div>',
        _rewrite_code_block,
        main_html,
        flags=re.DOTALL,
    )

    main_html = re.sub(
        r'<div\s+class="cell-output-display"[^>]*>.*?</div>\s*(?=</div>|<div|<section|<h[1-6]|$)',
        "<p><em>[Rich HTML output — view on the documentation site]</em></p>\n",
        main_html,
        flags=re.DOTALL,
    )

    main_html = re.sub(r"</section>\s*", "", main_html)

    def _convert_callouts(html):
        result = []
        pos = 0
        while True:
            start = html.find('<div class="callout ', pos)
            if start == -1:
                result.append(html[pos:])
                break
            result.append(html[pos:start])
            depth = 0
            i = start
            end = len(html)
            while i < end:
                open_m = re.match(r"<div[\s>]", html[i:])
                close_m = re.match(r"</div>", html[i:])
                if open_m:
                    depth += 1
                    i += open_m.end()
                elif close_m:
                    depth -= 1
                    i += close_m.end()
                    if depth == 0:
                        break
                else:
                    i += 1
            callout_html = html[start:i]
            type_m = re.search(r"callout-(tip|note|warning|important|caution)", callout_html)
            callout_type = type_m.group(1).capitalize() if type_m else "Note"
            title_m = re.search(
                r'<div\s+class="callout-title-container[^"]*">\s*'
                r"(?:<span[^>]*>[^<]*</span>)?\s*(.*?)\s*</div>",
                callout_html,
                re.DOTALL,
            )
            title_text = title_m.group(1).strip() if title_m else ""
            body_m = re.search(
                r'<div\s+class="callout-body-container[^"]*">\s*(.*?)\s*</div>',
                callout_html,
                re.DOTALL,
            )
            body_html = body_m.group(1).strip() if body_m else ""
            if title_text:
                header = f"<p><strong>{callout_type}: {title_text}</strong></p>"
            else:
                header = f"<p><strong>{callout_type}</strong></p>"
            result.append(f"<blockquote>{header}\n{body_html}</blockquote>")
            pos = i
        return "".join(result)

    main_html = _convert_callouts(main_html)

    def _param_dl_to_html(m):
        """Convert a <dl> block of parameters to simple HTML paragraphs."""
        dl_html = m.group(0)
        items = []
        dt_dd_pattern = re.compile(
            r'<dt>.*?<span class="(?:parameter-name|doc-parameter-name)">\s*<strong>(.*?)</strong>\s*</span>'
            r'(?:.*?<span class="(?:parameter-annotation|doc-parameter-annotation)">(.*?)</span>)?'
            r'(?:.*?<span class="(?:parameter-default|doc-parameter-default)">(.*?)</span>)?'
            r".*?</dt>\s*<dd>\s*(.*?)\s*</dd>",
            re.DOTALL,
        )
        for dt_dd in dt_dd_pattern.finditer(dl_html):
            name = dt_dd.group(1).strip().strip("`")
            annotation = dt_dd.group(2) or ""
            default = dt_dd.group(3) or ""
            desc = dt_dd.group(4) or ""
            desc = re.sub(r"</?p>", "", desc).strip()

            sig = f"<strong>{name}</strong>"
            if annotation:
                sig += f" : <code>{annotation.strip()}</code>"
            if default:
                sig += f" = <code>{default.strip()}</code>"

            line = f"<li>{sig}"
            if desc:
                line += f" &mdash; {desc}"
            line += "</li>"
            items.append(line)

        if items:
            return "<ul>\n" + "\n".join(items) + "\n</ul>"
        return dl_html

    main_html = re.sub(
        r"<dl>.*?</dl>",
        _param_dl_to_html,
        main_html,
        flags=re.DOTALL,
    )

    main_html = re.sub(
        r'<p\s+class="doc-description"[^>]*>',
        "<p><em>",
        main_html,
    )

    main_html = re.sub(
        r'<a\s+href="#cb\d+-\d+"[^>]*></a>',
        "",
        main_html,
    )

    main_html = re.sub(
        r'<span\s+class="(?:sig-name|sig-class|cn-none|cn-bool)">(.*?)</span>',
        r"\1",
        main_html,
    )

    main_html = re.sub(
        r'<span\s+class="(?:parameter-|doc-parameter-)[^"]*"[^>]*>(.*?)</span>',
        r"\1",
        main_html,
    )

    main_html = re.sub(
        r"<code>\s*(.*?)\s*</code>",
        r"<code>\1</code>",
        main_html,
    )

    return html_file, rel, main_html


def _convert_one_page(args: tuple[str, str, str]) -> tuple[str, bool, str]:
    """Convert a single prepared HTML page to Markdown via pandoc.

    Returns (rel_path, success, error_message).
    """
    import subprocess

    html_file, rel, main_html = args

    try:
        result = subprocess.run(
            ["quarto", "pandoc", "-f", "html", "-t", "gfm", "--wrap=none"],
            input=main_html,
            capture_output=True,
            **_SUBPROCESS_TEXT_KWARGS,
            timeout=30,
        )

        if result.returncode != 0:
            return rel, False, f"pandoc error: {result.stderr.strip()[:200]}"

        md_content = _postprocess_markdown_content(result.stdout, rel)

        md_file = html_file.rsplit(".", 1)[0] + ".md"
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(md_content)

        return rel, True, ""

    except subprocess.TimeoutExpired:
        return rel, False, "pandoc timeout"
    except (OSError, subprocess.SubprocessError) as e:
        return rel, False, str(e)


def generate_markdown_pages():
    """
    Create a .md companion for every .html page in _site/.

    Steps for each page:
      1. Extract the <main id="quarto-document-content"> inner HTML.
      2. Strip navigation-only elements (prev/next links, breadcrumbs).
      3. Replace _repr_html_ output blocks with a text placeholder.
      4. Pipe the HTML fragment through ``quarto pandoc -f html -t gfm``.
      5. Write the result as a .md file next to the original .html.

    Pandoc conversion is parallelized across a thread pool to reduce the
    per-subprocess overhead from ~300 sequential spawns to batched execution.
    """
    import shutil
    from concurrent.futures import ThreadPoolExecutor, as_completed

    pandoc_cmd = shutil.which("quarto")
    if pandoc_cmd is None:
        print("Warning: 'quarto' not found on PATH; skipping .md generation")
        return

    print("Generating Markdown (.md) pages...")

    work_items = []
    for html_file in all_html_files:
        html_file_path, rel, main_html = _prepare_html_for_pandoc(html_file)
        if main_html is not None:
            work_items.append((html_file_path, rel, main_html))

    generated = 0
    errors = 0

    max_workers = min(8, os.cpu_count() or 4)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_convert_one_page, item): item for item in work_items}
        for future in as_completed(futures):
            rel, success, err_msg = future.result()
            if success:
                generated += 1
            else:
                print(f"  {err_msg} for {rel}")
                errors += 1

    print(f"Generated {generated} Markdown page(s) ({errors} error(s))")


if _gd_options.get("markdown_pages", True):
    generate_markdown_pages()
print("##GD:PASS:Markdown pages generated", flush=True)


# ══════════════════════════════════════════════════════════════════════════════
# INJECT MARKDOWN ALTERNATE LINKS
# ══════════════════════════════════════════════════════════════════════════════
# Add <link rel="alternate" type="text/markdown"> to HTML pages that have a
# corresponding .md companion, so AI agents can discover the Markdown variant.

if _gd_options.get("markdown_pages", True):
    print("\nInjecting Markdown alternate links...")
    _md_alt_count = 0
    for html_file in glob.glob("_site/**/*.html", recursive=True):
        md_companion = html_file.rsplit(".", 1)[0] + ".md"
        if not os.path.isfile(md_companion):
            continue
        try:
            with open(html_file, "r", encoding="utf-8") as f:
                content = f.read()

            # Skip if already has a markdown alternate link
            if 'rel="alternate" type="text/markdown"' in content:
                continue

            # Build the href (just the filename — same directory)
            md_basename = os.path.basename(md_companion)
            alt_tag = f'<link rel="alternate" type="text/markdown" href="{md_basename}">'
            modified = content.replace("</head>", f"  {alt_tag}\n</head>", 1)

            if modified != content:
                with open(html_file, "w", encoding="utf-8") as f:
                    f.write(modified)
                _md_alt_count += 1
        except (OSError, ValueError) as e:
            print(f"  Error injecting md alternate for {html_file}: {e}")

    if _md_alt_count > 0:
        print(f"   Injected alternate links in {_md_alt_count} page(s)")
print("##GD:PASS:Markdown alternate links injected", flush=True)
