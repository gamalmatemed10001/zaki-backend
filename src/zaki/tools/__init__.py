from zaki.tools import calendar, drive, gmail, notes, reminders, system, tasks, telephony, web_search, whatsapp
from zaki.tools.base import ToolContext, ToolDefinition
from zaki.tools.registry import ToolRegistry

ALL_TOOLS: list[ToolDefinition] = [
    *calendar.TOOLS,
    *gmail.TOOLS,
    *drive.TOOLS,
    *tasks.TOOLS,
    *notes.TOOLS,
    *web_search.TOOLS,
    *system.TOOLS,
    *whatsapp.TOOLS,
    *telephony.TOOLS,
    *reminders.TOOLS,
]

registry = ToolRegistry(ALL_TOOLS)

__all__ = ["ToolContext", "ToolDefinition", "ToolRegistry", "registry"]
