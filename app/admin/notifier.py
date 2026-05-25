from dataclasses import dataclass


@dataclass(slots=True)
class AnnouncementMessage:
    title: str
    content: str
    channels: list[str]


class AnnouncementNotifier:
    async def publish(self, message: AnnouncementMessage) -> dict[str, str]:
        return {channel: "queued" for channel in message.channels}
