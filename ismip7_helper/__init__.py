"""Helpers for working with the ISMIP7 datasets published on source.coop.

The ``conventions`` subpackage holds typed (pydantic) models of the ISMIP7
naming and metadata conventions, for both the input forcing datasets under
``s3://us-west-2.opendata.source.coop/ismip/`` and model-output submissions.
``index`` lists the bucket and parses every key into those models; ``report``
summarises what parsed, what did not, and why.
"""

SOURCE_BUCKET = "us-west-2.opendata.source.coop"
SOURCE_REGION = "us-west-2"
SOURCE_ACCOUNT = "ismip"
SOURCE_DATA_URL = f"s3://{SOURCE_BUCKET}/{SOURCE_ACCOUNT}"

# Where the englacial Icechunk stores are written (one repo per region).
TARGET_PREFIX = "englacial/ismip7"

__all__ = [
    "SOURCE_ACCOUNT",
    "SOURCE_BUCKET",
    "SOURCE_DATA_URL",
    "SOURCE_REGION",
    "TARGET_PREFIX",
]
