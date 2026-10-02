"""Точка входа GUI-приложения."""

from __future__ import annotations

import sys


def main() -> int:
    from chat_multi_cli.ui.app import run

    return run()


if __name__ == "__main__":
    sys.exit(main())
