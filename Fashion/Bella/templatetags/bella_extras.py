import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

# Lines starting with -, *, or • in the admin textarea become <li> items.
BULLET_PREFIX = re.compile(r"^\s*[-*\u2022]\s+")


@register.filter(name="format_description")
def format_description(text):
    """
    Turns a plain-text product description into readable HTML:
    - blank-line-separated blocks become paragraphs
    - a run of lines starting with -, *, or • becomes a bullet list

    Lets the admin keep typing into a plain textarea - e.g.

        Soft, breathable satin lining that protects your hair overnight.

        - Adjustable drawstring for a snug fit
        - Machine washable
        - One size fits most

    - and it renders as an intro paragraph plus a proper bullet list.
    """
    if not text:
        return ""

    blocks = re.split(r"\n\s*\n", text.strip())
    html_parts = []

    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        if all(BULLET_PREFIX.match(line) for line in lines):
            items = "".join(
                f"<li>{escape(BULLET_PREFIX.sub('', line))}</li>" for line in lines
            )
            html_parts.append(f"<ul class='product-detail-list'>{items}</ul>")
        else:
            paragraph = " ".join(escape(line) for line in lines)
            html_parts.append(f"<p>{paragraph}</p>")

    return mark_safe("".join(html_parts))