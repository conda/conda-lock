from pydantic import BaseModel


class StrictModel(BaseModel, extra="forbid"):
    """A Pydantic BaseModel forbidding extra fields and encoding frozensets as lists"""
