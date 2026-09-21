"""Email bodies.

TWO THINGS THAT ARE EASY TO GET WRONG HERE, and invisible when you do:

1. Translate the template, then interpolate. ``_(f"...{link}...")`` would make
   the message id contain the actual URL, which no catalogue entry can match.
2. The link goes through ``html.escape`` with ``quote=True`` before it reaches
   an ``href``: it is not attacker-controlled today, but an unescaped value in an
   HTML attribute is one refactor away from being a hole.

Each translatable sentence is its own message id rather than one blob of markup,
so a translator sees prose instead of HTML, and changing the layout does not
invalidate every translation.
"""

from __future__ import annotations

from html import escape

from archihub.core.i18n import gettext as _


def _wrap(paragraphs: list[str]) -> str:
    body = "\n        ".join(paragraphs)
    return f"""
    <html>
    <body>
        {body}
    </body>
    </html>
    """


def forgot_password_template(link: str) -> str:
    safe_link = escape(link, quote=True)
    return _wrap(
        [
            f"<p>{_('Hello,')}</p>",
            f"<p>{_('You have one day to reset your password, click on the following link:')}</p>",
            f'<a href="{safe_link}">{_("Reset password")}</a>',
            f"<p>{_('If you did not request a password reset, please ignore this email.')}</p>",
        ]
    )


def new_user_verification_template(link: str) -> str:
    safe_link = escape(link, quote=True)
    return _wrap(
        [
            f"<p>{_('Hello,')}</p>",
            f"<p>{_('Thank you for registering, click on the following link to verify your account:')}</p>",
            f'<a href="{safe_link}">{_("Verify account")}</a>',
        ]
    )
