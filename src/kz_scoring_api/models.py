import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

IIN_PATTERN = re.compile(r"^\d{12}$")
PHONE_PATTERN = re.compile(r"^\d{6,15}$")


class LookupRequest(BaseModel):
    iin: str
    phone: str | None = None

    @field_validator("iin")
    @classmethod
    def _validate_iin(cls, v: str) -> str:
        if not IIN_PATTERN.match(v):
            raise ValueError("iin must be exactly 12 digits")
        return v

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not PHONE_PATTERN.match(v):
            raise ValueError("phone must be msisdn without '+' (6-15 digits)")
        return v


FeatureRow = dict[str, Any]


class MultiInputItem(BaseModel):
    iin: str = Field(...)
    phone: str | None = None

    @field_validator("iin")
    @classmethod
    def _validate_iin(cls, v: str) -> str:
        if not IIN_PATTERN.match(v):
            raise ValueError("iin must be exactly 12 digits")
        return v

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not PHONE_PATTERN.match(v):
            raise ValueError("phone must be msisdn without '+' (6-15 digits)")
        return v
