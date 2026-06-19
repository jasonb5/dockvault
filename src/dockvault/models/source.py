from typing import Annotated, Literal

from pydantic import BaseModel, Field


class FileSource(BaseModel):
    type: Literal["files"]

    volume_name: str


BackupSource = Annotated[FileSource, Field(discriminator="type")]
