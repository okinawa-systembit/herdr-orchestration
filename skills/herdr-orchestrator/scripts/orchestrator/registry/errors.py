from __future__ import annotations


class RegistryError(Exception):
    def __init__(
        self,
        code: str,
        *,
        message: str | None = None,
        retryable: bool = False,
        task_id: str | None = None,
    ) -> None:
        self.code = code
        self.message = message or code
        self.retryable = retryable
        self.task_id = task_id
        super().__init__(self.message)
