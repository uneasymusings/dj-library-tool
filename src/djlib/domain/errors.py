"""Actionable application errors independent of any transport."""

from dataclasses import dataclass


@dataclass
class AppError(Exception):
    code: str
    message: str
    status: int = 400
    retryable: bool = False
    # Optional structured context for callers (e.g. what listeners named); omitted when None.
    details: dict | None = None

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> dict:
        error = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details is not None:
            error["details"] = self.details
        return error
