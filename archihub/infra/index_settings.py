"""Analysis settings applied when a search index is created.

Port of ``app/utils/index/spanish_settings.py``, unchanged in content.

It is Spanish-only, and that is a limitation rather than a decision: the
stemmer and stop-word list are fixed at index-creation time, so an instance
whose material is in another language gets Spanish stemming applied to it. The
interface language setting has no bearing on this - changing it does not
reanalyse anything. Making this configurable means making it a *setting read at
regenerate time*, which is a change to the index lifecycle rather than to this
file; recorded in BACKEND_FINDINGS (P12) rather than done here.
"""

from __future__ import annotations

SPANISH_SETTINGS: dict = {
    "analysis": {
        "analyzer": {
            "analyzer_spanish": {
                "tokenizer": "standard",
                "filter": [
                    "lowercase",
                    "asciifolding",
                    "default_spanish_stopwords",
                    "default_spanish_stemmer",
                ],
            }
        },
        "filter": {
            "default_spanish_stemmer": {"type": "stemmer", "name": "spanish"},
            "default_spanish_stopwords": {"type": "stop", "stopwords": ["_spanish_"]},
        },
    }
}
