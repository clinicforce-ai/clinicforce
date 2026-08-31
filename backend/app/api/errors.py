"""Application-level failures that routes translate into safe HTTP responses."""


class IntegrationFailure(Exception):
    """Records which integration component caused a recoverable route failure."""

    def __init__(self, component: str, cause: Exception) -> None:
        self.component = component
        self.cause = cause
        super().__init__(f"{component}: {cause}")
