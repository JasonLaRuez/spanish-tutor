"""Word-bank-constrained Spanish tutor."""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="spanish-tutor", description="Word-bank-constrained Spanish tutor."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the web app (API + UI)")
    serve.add_argument("--host", default="127.0.0.1", help="default: this machine only")
    serve.add_argument("--port", type=int, default=8000)
    commands.add_parser("openapi", help="print the API's OpenAPI schema (for the UI's types)")
    args = parser.parse_args()

    if args.command == "serve":
        from spanish_tutor.api.app import serve as run

        run(args.host, args.port)
    elif args.command == "openapi":
        import json

        from spanish_tutor.api.app import create_app

        # The schema is built from the routes alone; nothing is loaded.
        print(json.dumps(create_app().openapi(), ensure_ascii=False, indent=1))
