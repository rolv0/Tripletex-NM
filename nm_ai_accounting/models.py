from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class TaskFile(BaseModel):
    filename: str
    content_base64: str
    mime_type: str


class TripletexCredentials(BaseModel):
    base_url: str
    session_token: str


class SolveRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    prompt: str
    files: list[TaskFile] = []
    tripletex_credentials: TripletexCredentials


class SolveResponse(BaseModel):
    status: str = "completed"
