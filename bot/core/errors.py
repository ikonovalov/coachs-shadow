class DomainError(Exception):
    """Expected business error; `message` is shown to the user as is."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class PermissionDenied(DomainError):
    pass


class NotFound(DomainError):
    pass


class InvalidAction(DomainError):
    pass
