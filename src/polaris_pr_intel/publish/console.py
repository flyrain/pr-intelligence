from __future__ import annotations

class ConsolePublisher:
    def publish_daily_report(self, markdown: str) -> None:
        print(markdown)
