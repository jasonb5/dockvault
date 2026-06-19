from typing import Annotated, Literal
from pydantic import BaseModel, Field, TypeAdapter


class ResticStatus(BaseModel):
    message_type: Literal["status"]

    percent_done: float | None = None
    total_files: int | None = None
    files_done: int | None = None
    total_bytes: int | None = None
    bytes_done: int | None = None
    current_files: list[str] = Field(default_factory=list)


class ResticSummary(BaseModel):
    message_type: Literal["summary"]

    files_new: int = 0
    files_changed: int = 0
    files_unmodified: int = 0

    dirs_new: int = 0
    dirs_changed: int = 0
    dirs_unmodified: int = 0

    data_blobs: int = 0
    tree_blobs: int = 0
    data_added: int = 0

    total_files_processed: int = 0
    total_bytes_processed: int = 0

    total_duration: float | None = None
    snapshot_id: str | None = None


class ResticExitError(BaseModel):
    message_type: Literal["exit_error"]

    code: int
    message: str

    # some commands may include additional details
    command: str | None = None


ResticMessage = Annotated[
    ResticStatus | ResticSummary | ResticExitError,
    Field(discriminator="message_type"),
]

ResticMessageAdapter: TypeAdapter[ResticMessage] = TypeAdapter(ResticMessage)
