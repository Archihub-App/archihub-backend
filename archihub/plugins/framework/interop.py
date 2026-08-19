"""Capabilities one plugin provides to another.

There is exactly one such dependency among the five in scope. Expressed as a
direct import across plugin package boundaries it would read:

```python
try:
    from app.plugins.filesProcessing.utils.DocumentProcessing import convert_to_pdf_with_libreoffice
except Exception as e:
    raise Exception('Error al importar el módulo del plugin para el procesamiento de documentos: ' + str(e))
```

Three things are wrong with that, and only the third is obvious:

1. It couples ``liquidText`` to ``filesProcessing``'s *internal file layout*.
   Moving a helper inside filesProcessing breaks a different plugin.
2. It bypasses activation entirely — the import succeeds whether or not
   ``filesProcessing`` is active on this instance, so a deactivated plugin's
   code still runs.
3. The failure arrives from inside a Celery task as a sentence about a Python
   import, which tells an operator nothing about what to do.

A provider registers here; a consumer asks for the capability by name and gets a
clear refusal naming the plugin to activate. Registration happens when the
providing plugin is built, so it follows activation rather than the filesystem.
"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)

#: capability name -> (providing plugin slug, callable)
_providers: dict[str, tuple[str, Callable]] = {}


class CapabilityUnavailable(RuntimeError):
    """No active plugin provides the requested capability."""


def provide(capability: str, slug: str, func: Callable) -> None:
    """Register ``func`` as this instance's provider of ``capability``."""
    existing = _providers.get(capability)
    if existing and existing[0] != slug:
        logger.warning(
            "Plugin %s is replacing %s as the provider of %s", slug, existing[0], capability
        )
    _providers[capability] = (slug, func)


def get(capability: str, *, needed_by: str = "", provider_hint: str = "") -> Callable:
    """The registered provider, or a refusal that says what to activate."""
    entry = _providers.get(capability)
    if entry is None:
        hint = f" Activate the {provider_hint} plugin." if provider_hint else ""
        context = f" ({needed_by} needs it.)" if needed_by else ""
        raise CapabilityUnavailable(
            f"No active plugin provides '{capability}'.{hint}{context}"
        )
    return entry[1]


def has(capability: str) -> bool:
    return capability in _providers


def reset() -> None:
    """Drop every registration. Used by tests and when remounting."""
    _providers.clear()


# ---------------------------------------------------------------------------
# The capabilities themselves
# ---------------------------------------------------------------------------

PDF_CONVERSION = "document.to_pdf"

#: Derive the web-sized versions of a stored file and record the result on it.
#: `views` needs this for a thumbnail: the image is stored as a record, but a
#: record is only *renderable* once a derivative exists at
#: ``<web_files>/<path>_medium.jpg``.
IMAGE_DERIVATIVES = "file.derivatives"


def convert_to_pdf(source, destination) -> None:
    """Convert a document to PDF using whichever plugin provides it."""
    converter = get(
        PDF_CONVERSION, needed_by="PDF export", provider_hint="filesProcessing"
    )
    converter(source, destination)


def derive_web_versions(record: dict) -> bool:
    """Produce a stored file's web derivatives. Returns whether it did.

    A **core** domain reaching for a plugin capability, which is worth being
    explicit about. A view's thumbnail is not optional decoration: the image is
    uploaded through the view form and is unrenderable until something turns it
    into `_medium.jpg`. The legacy views service imported
    ``app.plugins.filesProcessing`` directly for exactly this, which ran the
    plugin's code whether or not it was active.

    Going through the registry keeps that following activation, and gives an
    instance with ``filesProcessing`` switched off a refusal naming what to turn
    on rather than an ImportError.
    """
    processor = get(
        IMAGE_DERIVATIVES, needed_by="the view thumbnail", provider_hint="filesProcessing"
    )
    return bool(processor(record))
