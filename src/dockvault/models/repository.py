from typing import Annotated, Literal

from pydantic import BaseModel, Field


class RepositoryConfig(BaseModel):
    password_env: str = "RESTIC_PASSWORD"


class LocalRepository(RepositoryConfig):
    type: Literal["local"]
    path: str


BackupRepository = Annotated[
    LocalRepository,
    Field(discriminator="type"),
]
