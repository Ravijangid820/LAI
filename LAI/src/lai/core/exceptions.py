"""Exception hierarchy for the LAI platform."""


class LAIError(Exception):
    """Base exception for all LAI errors."""


# Service errors
class ServiceUnavailableError(LAIError):
    """An external service (embedding, LLM, reranker) is unavailable."""


class EmbeddingError(ServiceUnavailableError):
    """Embedding service error."""


class LLMError(ServiceUnavailableError):
    """LLM service error."""


class RerankerError(ServiceUnavailableError):
    """Reranker service error."""


# Retrieval errors
class RetrievalError(LAIError):
    """Error during document retrieval."""


class EmptyRetrievalError(RetrievalError):
    """No chunks found for query."""


# Database errors
class DatabaseError(LAIError):
    """Database operation error."""


class SchemaError(DatabaseError):
    """Multi-tenancy schema error."""


# Document processing errors
class DocumentProcessingError(LAIError):
    """Error during document processing."""


class UnsupportedFormatError(DocumentProcessingError):
    """Unsupported document format."""


class FileTooLargeError(DocumentProcessingError):
    """File exceeds size limit."""


# Input validation
class InputValidationError(LAIError):
    """User input validation error."""


class QueryTooLongError(InputValidationError):
    """Query exceeds max length."""
