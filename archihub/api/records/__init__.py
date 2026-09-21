"""Records: the files attached to catalogued resources.

A record is one stored file plus everything known about it - its hash, its
detected media type, the resources it belongs to, and whatever the processing
plugins have derived from it (transcriptions, OCR blocks, derivatives).

Records are deduplicated by content hash: the same file uploaded against two
resources is stored once and gains a second parent.
"""
