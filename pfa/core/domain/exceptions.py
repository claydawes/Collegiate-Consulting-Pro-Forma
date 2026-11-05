"""Domain-level exceptions.

All application-specific exceptions inherit from CCPError so calling code
(the Streamlit UI, scripts, tests) can catch "anything this platform raised
on purpose" in one place, distinct from unexpected third-party/library
errors that should propagate and get logged as bugs rather than handled as
expected control flow.
"""

from __future__ import annotations


class CCPError(Exception):
    """Base exception for all Collegiate Consulting Platform domain errors."""


class ConfigurationError(CCPError):
    """Application or business configuration is missing, malformed, or invalid."""


class DataDictionaryError(CCPError):
    """A data dictionary definition is invalid, or a field lookup failed."""


class ValidationRuleError(CCPError):
    """A validation rule set configuration is invalid."""


class ExtractionError(CCPError):
    """Local extraction of a source document failed unrecoverably."""


class AIProviderError(CCPError):
    """An AI provider call failed."""


class AIProviderNotConfiguredError(AIProviderError):
    """An AI-dependent operation was invoked but no provider is configured.

    Raised lazily - only at the moment an AI-dependent extraction step
    actually runs - never at application startup. The app must boot and run
    local-extraction-only workflows with zero Anthropic configuration.
    """


class DuplicateDocumentError(CCPError):
    """A duplicate (or near-duplicate) source document was detected."""


class RepositoryError(CCPError):
    """A persistence-layer operation failed."""


class StorageError(CCPError):
    """A file storage or retention-policy operation failed."""
