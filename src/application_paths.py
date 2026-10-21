"""Locate portable support files while retaining the earlier flat layout."""

from pathlib import Path


def application_data_directory(installation_root: Path) -> Path:
    root = Path(installation_root)
    support = root / "app"
    return support if support.is_dir() else root


def installation_directory(data_directory: Path) -> Path:
    data = Path(data_directory)
    if data.name == "app" and (data.parent / "S2P-XInput-Lite.exe").is_file():
        return data.parent
    return data
