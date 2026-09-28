"""Optional convenience for configuring how sdk_primer logs.

sdk_primer logs through loguru's global `logger`, which already writes
INFO-and-above to stderr by default - you don't need to call anything here
to see logs.

configure_logging() is a convenience for callers who want a different
level, format, or destination without reaching into loguru directly. It
calls `logger.remove()` first, which clears *every* handler on loguru's
global logger, not just sdk_primer's - fine for a script or a small app,
but if the host application already configures loguru itself, let it own
that configuration instead of calling this.
"""

import sys
from typing import TextIO

from loguru import logger

# APIClient.request() binds a fresh request_id per logical call (see
# client.py) so every log line from one call - including every retry
# attempt - is grep-able as a unit. A default here means a host format
# string that references {extra[request_id]} (or serialize=True, which
# includes every extra field automatically) never KeyErrors on a log line
# from outside request() that has nothing bound.
logger.configure(extra={"request_id": None})


def configure_logging(level: str = "DEBUG", sink: TextIO = sys.stderr) -> None:
    """Replace loguru's default handler with one at the given level."""
    logger.remove()
    logger.add(sink, level=level)
