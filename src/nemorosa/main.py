"""Main entry point for nemorosa."""

import sys

from .cli import main


def setup_event_loop():
    """Setup the best available event loop for the current platform."""
    try:
        if sys.platform == "win32":
            import winloop  # pyright: ignore[reportMissingImports]

            winloop.install()
        else:
            import uvloop  # pyright: ignore[reportMissingImports]

            uvloop.install()
    except Exception as e:
        print(f"Event loop setup warning: {e}, using default asyncio")


if __name__ == "__main__":
    setup_event_loop()
    main()
