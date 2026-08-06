from __future__ import annotations

import argparse

from .seed import initialize_database, seed_demo_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram Test Mini App boshqaruv CLI")
    parser.add_argument("command", choices=["init-db", "seed-demo"])
    args = parser.parse_args()
    initialize_database()
    if args.command == "seed-demo":
        seed_demo_data()
        print("Namuna ma'lumotlar qo'shildi.")
    else:
        print("Ma'lumotlar bazasi tayyor.")


if __name__ == "__main__":
    main()
