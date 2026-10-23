#!/usr/bin/env python3
"""Django 命令行入口。"""
import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError("没装 Django。先执行：pip install -r requirements.txt") from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
