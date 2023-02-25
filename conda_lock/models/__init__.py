from pydantic import BaseModel


class StrictModel(BaseModel):
    """A Pydantic BaseModel forbidding extra fields and encoding frozensets as lists"""

    class Config:
        extra = "forbid"
        json_encoders = {
            frozenset: list,
        }
