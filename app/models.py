from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional
from enum import Enum


class EventStatus(str, Enum):
    active = "active"
    sent = "sent"
    cancelled = "cancelled"


class Event(BaseModel):
    id: str
    user_id: str
    display_name: str
    event_name: str
    event_date: str          # YYYY-MM-DD
    event_time: str          # HH:MM
    reminder_minutes: int
    status: EventStatus
    created_at: str
    location: str = ""
    responsible: str = ""    # pipe-separated list
    participants: str = ""   # pipe-separated list
    details: str = ""
    group_id: str = ""       # LINE group/room ID; empty for direct messages
    chat_type: str = "user"  # "user" | "group" | "room"

    @property
    def target_id(self) -> str:
        """Push destination — group/room when available, otherwise the user."""
        return self.group_id if self.group_id else self.user_id

    def reminder_datetime(self) -> datetime:
        from datetime import timedelta
        event_dt = datetime.strptime(f"{self.event_date} {self.event_time}", "%Y-%m-%d %H:%M")
        return event_dt - timedelta(minutes=self.reminder_minutes)

    def event_datetime(self) -> datetime:
        return datetime.strptime(f"{self.event_date} {self.event_time}", "%Y-%m-%d %H:%M")

    def responsible_list(self) -> list[str]:
        return [r.strip() for r in self.responsible.split("|") if r.strip()]

    def participants_list(self) -> list[str]:
        return [p.strip() for p in self.participants.split("|") if p.strip()]


class CreateEventRequest(BaseModel):
    user_id: str = "dashboard"
    display_name: str = "Dashboard User"
    event_name: str
    event_date: str
    event_time: str
    reminder_minutes: int = 60
    location: str = ""
    responsible: str = ""
    participants: str = ""
    details: str = ""
    group_id: str = ""
    chat_type: str = "user"

    @field_validator("event_date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        datetime.strptime(v, "%Y-%m-%d")
        return v

    @field_validator("event_time")
    @classmethod
    def validate_time(cls, v: str) -> str:
        datetime.strptime(v, "%H:%M")
        return v


class UpdateEventRequest(BaseModel):
    event_name: Optional[str] = None
    event_date: Optional[str] = None
    event_time: Optional[str] = None
    reminder_minutes: Optional[int] = None
    status: Optional[EventStatus] = None
    location: Optional[str] = None
    responsible: Optional[str] = None
    participants: Optional[str] = None
    details: Optional[str] = None
