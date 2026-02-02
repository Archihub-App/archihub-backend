from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape


def build_rss(response, base_url, link_template, feed_title, feed_description):
    base_url = (base_url or '').rstrip('/')
    link_template = link_template or '/resource/{id}'
    feed_title = feed_title or 'ArchiHUB Blog'
    feed_description = feed_description or 'Blog feed'

    items = response.get('resources', [])

    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">')
    lines.append('<channel>')
    lines.append(f'<title>{escape(feed_title)}</title>')
    lines.append(f'<link>{escape(base_url + "/")}</link>')
    lines.append(f'<description>{escape(feed_description)}</description>')

    for resource in items:
        title = _get_nested_value(resource, 'metadata.firstLevel.title')
        if not title:
            title = resource.get('title') or resource.get('ident') or resource.get('id') or 'Untitled'

        item_link = _build_link(base_url, link_template, resource)
        article = resource.get('article', '') or ''
        created_at = resource.get('createdAt')
        pub_date = _format_rss_date(created_at)
        guid = resource.get('id') or resource.get('ident') or item_link

        lines.append('<item>')
        lines.append(f'<title>{escape(str(title))}</title>')
        if item_link:
            lines.append(f'<link>{escape(item_link)}</link>')
        if guid:
            lines.append(f'<guid isPermaLink="false">{escape(str(guid))}</guid>')
        if pub_date:
            lines.append(f'<pubDate>{escape(pub_date)}</pubDate>')
        if article:
            lines.append(f'<description>{escape(article)}</description>')
            lines.append(f'<content:encoded><![CDATA[{_safe_cdata(article)}]]></content:encoded>')
        lines.append('</item>')

    lines.append('</channel>')
    lines.append('</rss>')
    return '\n'.join(lines)


def _build_link(base_url, link_template, resource):
    try:
        path = link_template.format(
            id=resource.get('id', ''),
            ident=resource.get('ident', ''),
            post_type=resource.get('post_type', ''),
        )
    except Exception:
        path = link_template

    if not path:
        return base_url + '/'

    if path.startswith('http://') or path.startswith('https://'):
        return path

    if not path.startswith('/'):
        path = '/' + path

    return base_url + path


def _format_rss_date(value):
    if not value:
        return None

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    else:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return format_datetime(dt)


def _get_nested_value(data, path):
    current = data
    for part in path.split('.'):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _safe_cdata(text):
    return text.replace(']]>', ']]]]><![CDATA[>')
