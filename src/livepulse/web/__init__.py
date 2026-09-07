"""The web interface.

FastAPI over the same bus and repositories the rest of the process uses. The
web layer is *a subscriber*, not an owner: it edits state through the same
stores everything else reads, and announces changes on the same bus — so the
UI, the scheduler, and the strip can never disagree the way V1's two
processes did.
"""

from livepulse.web.app import WebContext, create_app

__all__ = ["WebContext", "create_app"]
