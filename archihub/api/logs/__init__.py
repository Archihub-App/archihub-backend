"""Audit log - who did what, when.

Distinct from application logging (``archihub/core/logging.py``): this records
business actions with their metadata, is stored in MongoDB, and is a product
feature rather than an operational one.
"""
