from dataclasses import dataclass
from typing import Literal

TaskStatus = Literal["queued", "parsing", "downloading", "completed", "failed"]


@dataclass
class DownloadTask:
    id: str
    batch_id: str
    queue_index: int
    source_row: int
    url: str
    title: str
    quality: str
    save_path: str
    collection: str = ""
    naming_template: str = "({index})- {title}"
    status: TaskStatus = "queued"
    progress: int = 0
    error_message: str = ""

