"""An in-process cloud: records every request body and answers from a queue of canned replies."""
from wattshift_agent.client import CloudError


class FakeCloud:
    def __init__(self):
        self.bodies: list[dict] = []
        self.replies: list[dict] = []
        self.fail = False

    def reply(self, **fields) -> None:
        self.replies.append(fields)

    def health(self) -> None:
        if self.fail:
            raise CloudError("connection refused")

    def post_sync(self, body: dict) -> dict:
        if self.fail:
            raise CloudError("connection refused")
        self.bodies.append(body)
        extra = self.replies.pop(0) if self.replies else {}
        return {"decisions": [], "release_all": False, "next_poll_s": 30, **extra}
