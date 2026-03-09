from typing import List, Optional
from pydantic import BaseModel


class ExtractedIntent(BaseModel):
    intent: str  # "create_event" | "chat" | "missing_info"
    title: Optional[str] = None
    date: Optional[str] = None           # YYYY-MM-DD
    start_time: Optional[str] = None     # HH:MM (24h)
    duration_minutes: int = 60
    description: Optional[str] = None
    location: Optional[str] = None
    missing_fields: List[str] = []
    follow_up_question: Optional[str] = None
    chat_reply: Optional[str] = None


class CalendarEventRequest(BaseModel):
    title: str
    date: str           # YYYY-MM-DD
    start_time: str     # HH:MM (24h)
    duration_minutes: int = 60
    description: Optional[str] = None
    location: Optional[str] = None


class CalendarEventResult(BaseModel):
    event_id: Optional[str] = None
    link: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None
