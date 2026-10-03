"""Actionable application errors independent of any transport."""

from dataclasses import dataclass


@dataclass
class AppError(Exception):
    code: str
    message: str
    status: int = 400
    retryable: bool = False

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
