import argparse
import json


def main():
    parser = argparse.ArgumentParser(description="Бэкап под проверкой")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Локальная симуляция S3, без расходов в облаке")
    demo.add_argument("--output", default="artifacts/local-demo")
    args = parser.parse_args()
    if args.command == "demo":
        from backup_lab.demo import run

        print(json.dumps(run(args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
