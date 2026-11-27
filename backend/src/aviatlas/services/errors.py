"""Application failures translated into HTTP responses by the API layer."""
class ServiceError(Exception):
    pass

class InvalidInput(ServiceError):
    pass

class DocumentNotFound(ServiceError):
    pass

class DocumentNotReady(ServiceError):
    pass

class DocumentProcessingFailed(ServiceError):
    pass

class RetrievalUnavailable(ServiceError):
    pass

class GenerationUnavailable(ServiceError):
    pass

class GenerationFailed(ServiceError):
    pass

class DocumentBusy(ServiceError):
    pass


class CollectionNotFound(ServiceError):
    pass


class DuplicateDocument(ServiceError):
    pass


class ConversationNotFound(ServiceError):
    pass


class ConversationBusy(ServiceError):
    pass


class ChunkNotFound(ServiceError):
    pass


class CorrectionNotFound(ServiceError):
    pass
