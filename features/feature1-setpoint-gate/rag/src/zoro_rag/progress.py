"""Terminal progress on stderr; keep redirected logs and JSON output clean."""
import sys

from tqdm import tqdm


def progress(items, *, desc: str, unit: str):
    return tqdm(
        items, desc=desc, unit=unit, dynamic_ncols=True,
        file=sys.stderr, disable=not sys.stderr.isatty(),
    )
