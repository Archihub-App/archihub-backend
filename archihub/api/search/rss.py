"""The public blog feed.

A thin rendering of a blog-view search. It is a *public* surface, so it goes
through the same ``services.search`` with ``public=True`` — the feed cannot see
anything the public search cannot, which was not true of the legacy version:
`get_rss_feed` passed the caller's body to the same builder that let them choose
a publication state, so `?body={"status":"draft"}` published unreleased drafts
as an RSS feed (BACKEND_FINDINGS S28).

Everything interpolated into the XML is escaped, and the article body goes in a
CDATA section with its terminator neutralised — an article containing `]]>`
would otherwise close the section early and let its remaining markup become
feed structure.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

DEFAULT_TITLE = "ArchiHUB"
DEFAULT_LINK_TEMPLATE = "/resource/{id}"
SUMMARY_LENGTH = 250


def build(response: dict, *, base_url: str, link_template: str | None,
          title: str | None, description: str | None) -> str:
    """An RSS 2.0 document for a search response."""
    base = (base_url or "").rstrip("/")
    template = link_template or DEFAULT_LINK_TEMPLATE
    feed_title = title or DEFAULT_TITLE
    feed_description = description or DEFAULT_TITLE

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">',
        "<channel>",
        f"<title>{escape(feed_title)}</title>",
        f"<link>{escape(base + '/')}</link>",
        f"<description>{escape(feed_description)}</description>",
    ]

    for resource in response.get("resources") or []:
        lines.extend(_item(resource, base, template))

    lines.extend(["</channel>", "</rss>"])
    return "\n".join(lines)


def _item(resource: dict, base: str, template: str) -> list[str]:
    title = _title_of(resource)
    link = _link(base, template, resource)
    article = resource.get("article") or ""
    guid = resource.get("id") or resource.get("ident") or link

    lines = ["<item>", f"<title>{escape(str(title))}</title>"]
    if link:
        lines.append(f"<link>{escape(link)}</link>")
    if guid:
        lines.append(f'<guid isPermaLink="false">{escape(str(guid))}</guid>')

    published = _rss_date(resource.get("createdAt"))
    if published:
        lines.append(f"<pubDate>{escape(published)}</pubDate>")

    if article:
        summary = article[:SUMMARY_LENGTH] + ("..." if len(article) > SUMMARY_LENGTH else "")
        lines.append(f"<description>{escape(summary)}</description>")
        lines.append(f"<content:encoded><![CDATA[{_safe_cdata(article)}]]></content:encoded>")

    lines.append("</item>")
    return lines


def _title_of(resource: dict) -> str:
    metadata = resource.get("metadata")
    if isinstance(metadata, dict):
        first = metadata.get("firstLevel")
        if isinstance(first, dict) and first.get("title"):
            return first["title"]
    return resource.get("ident") or resource.get("id") or "Untitled"


def _link(base: str, template: str, resource: dict) -> str:
    try:
        path = template.format(
            id=resource.get("id") or "",
            ident=resource.get("ident") or "",
            post_type=resource.get("post_type") or "",
        )
    except (KeyError, IndexError, ValueError):
        # A template naming something a resource does not have is a
        # configuration mistake, not a reason to fail the whole feed.
        logger.info("Unusable RSS link template; falling back to the default")
        path = DEFAULT_LINK_TEMPLATE.format(id=resource.get("id") or "")
    return f"{base}{path if path.startswith('/') else '/' + path}"


def _safe_cdata(text: str) -> str:
    """Neutralise a CDATA terminator inside the payload.

    An article containing `]]>` would otherwise close the section early, and
    everything after it would be parsed as feed markup.
    """
    return str(text).replace("]]>", "]]&gt;")


def _rss_date(value) -> str | None:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return format_datetime(moment)
