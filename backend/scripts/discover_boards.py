"""Find companies' careers boards in the Internet Archive's URL index and hand them to the service.

The archive refuses connections from Google Cloud, where the service runs, so this pass runs elsewhere: monthly
in GitHub Actions (.github/workflows/discover-boards.yml), or by hand:

    cd backend
    API_URL=https://jobs.aicvstudio.app SCHEDULER_TOKEN=... python -m scripts.discover_boards

Sends the board addresses in batches to POST /api/system/boards, which keeps only hiring-system boards and
skips the ones it knows. Prints counts only.
"""

import os
import sys

import httpx

from app.connectors.sources import WaybackSource

BATCH = 5000


def main() -> int:
    api, token = os.environ["API_URL"].rstrip("/"), os.environ["SCHEDULER_TOKEN"]
    links = WaybackSource().every_link()
    print(f"{len(links)} board addresses from the archive")
    boards = new = 0
    with httpx.Client(timeout=120) as http:
        for start in range(0, len(links), BATCH):
            resp = http.post(f"{api}/api/system/boards", headers={"X-Scheduler-Token": token},
                             json={"links": links[start:start + BATCH], "source": "wayback"})
            resp.raise_for_status()
            boards, new = boards + resp.json()["boards"], new + resp.json()["new"]
    print(f"{boards} company boards sent, {new} new to the directory")
    return 0


if __name__ == "__main__":
    sys.exit(main())
