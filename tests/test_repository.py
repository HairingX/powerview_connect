"""What the repository may hold."""
import ipaddress
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]

ALLOWED_NETWORKS = [ipaddress.ip_network(net) for net in (
    "127.0.0.0/8", "0.0.0.0/32", "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24",
    "255.255.0.0/16")]
"""Loopback, the ranges RFC 5737 sets aside for documentation, and netmasks."""

DOCUMENTATION_MAC = "00:00:5e:00:53:"
"""The range RFC 7042 sets aside for documentation."""

ALLOWED_EMAILS = {"41898282+github-actions[bot]@users.noreply.github.com"}


def _tracked() -> list[Path]:
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    return [ROOT / name for name in listed.stdout.decode().split("\0") if name]


def _texts() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in _tracked():
        try:
            found.append((str(path.relative_to(ROOT)), path.read_text(encoding="utf-8")))
        except (UnicodeDecodeError, FileNotFoundError):
            continue
    return found


def test_the_tracked_files_are_found() -> None:
    assert any(name == "pyproject.toml" for name, _ in _texts())


@pytest.mark.parametrize(("name", "text"), _texts(), ids=lambda value: str(value)[:60])
def test_no_real_ip_mac_or_email_address_is_tracked(name: str, text: str) -> None:
    addresses: list[str] = []
    for match in re.finditer(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?![\w.])", text):
        try:
            address = ipaddress.ip_address(match.group(1))
        except ValueError:
            continue
        if not any(address in network for network in ALLOWED_NETWORKS):
            addresses.append(str(address))
    macs = [mac for mac in re.findall(r"(?<![\w:])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![\w:])", text)
            if not mac.lower().startswith(DOCUMENTATION_MAC)]
    emails = set(re.findall(r"[\w.+-]+@[A-Za-z][\w-]*(?:\.[\w-]+)*\.[A-Za-z]{2,}\b", text)) - ALLOWED_EMAILS
    assert (addresses, macs, emails) == ([], [], set()), name

