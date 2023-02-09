from pydantic import BaseModel


class StrictModel(BaseModel):
    class Config:
        extra = "forbid"
        json_encoders = {
            frozenset: list,
        }
