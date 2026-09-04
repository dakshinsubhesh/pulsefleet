"""
PulseFleet — Domain exceptions
Mapped to HTTP responses in main.py's exception handlers, so route
functions raise these instead of building HTTPException/JSON by hand.
"""


class NotFoundError(Exception):
    def __init__(self, message: str, error_code: str = "not_found"):
        self.message = message
        self.error_code = error_code
        super().__init__(message)


class ConflictError(Exception):
    def __init__(self, message: str, error_code: str = "conflict"):
        self.message = message
        self.error_code = error_code
        super().__init__(message)
