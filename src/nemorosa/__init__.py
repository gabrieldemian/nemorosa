"""Nemorosa - A specialized tool for cross-seeding torrents."""

__version__ = "0.0.1"
__author__ = "KyokoMiki"
__description__ = (
    "A specialized cross-seeding tool designed for music torrents, featuring "
    "automatic file mapping, partial matching, and seamless torrent injection"
)

from .cli import main

__all__ = ["main", "__version__", "__author__", "__description__"]
