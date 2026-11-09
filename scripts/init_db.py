from db import DB_PATH, init_schema


def main() -> None:
    init_schema()
    print(f"Initialized DB at {DB_PATH}")


if __name__ == "__main__":
    main()
