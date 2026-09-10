"""Application-level controlled exceptions.

Every raise here becomes a stable `{"error": {"code", "message"}}` JSON
response via the global handler in main.py - callers never see a stack
trace or internal path.
"""


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class UnsupportedFileTypeError(AppError):
    def __init__(self, message: str = "Only PDF / JPG / PNG documents are supported."):
        super().__init__("UNSUPPORTED_FILE_TYPE", message, status_code=415)


class EmptyFileError(AppError):
    def __init__(self, message: str = "The uploaded file is empty."):
        super().__init__("EMPTY_FILE", message, status_code=400)


class FileTooLargeError(AppError):
    def __init__(self, message: str):
        super().__init__("FILE_TOO_LARGE", message, status_code=413)


class CorruptedFileError(AppError):
    def __init__(self, message: str = "The uploaded file could not be read or is corrupted."):
        super().__init__("CORRUPTED_FILE", message, status_code=422)


class PageLimitExceededError(AppError):
    def __init__(self, message: str):
        super().__init__("PAGE_LIMIT_EXCEEDED", message, status_code=422)


class InvalidDocumentTypeError(AppError):
    def __init__(self, message: str):
        super().__init__("INVALID_DOCUMENT_TYPE", message, status_code=400)


class DocumentNotFoundError(AppError):
    def __init__(self, message: str):
        super().__init__("DOCUMENT_NOT_FOUND", message, status_code=404)


class ProcessingError(AppError):
    def __init__(self, message: str = "Document processing failed unexpectedly."):
        super().__init__("PROCESSING_ERROR", message, status_code=500)
