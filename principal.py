import os
import sys
import textwrap

from configurare import configure_logging, run_interaction


EXIT_COMMANDS = {"/exit", "exit", "quit", "iesire", "ieșire"}
HELP_COMMANDS = {"/help", "?"}
CLEAR_COMMANDS = {"/clear"}
MODEL_NAME = "@cf/meta/llama-3.1-8b-instruct"


def _supports_color():
    return (
        sys.stdout.isatty()
        and os.environ.get("TERM") != "dumb"
        and "NO_COLOR" not in os.environ
    )


def _color(text, code, enabled):
    if not enabled:
        return text
    return f"\033[{code}m{text}\033[0m"


def _show_banner(color):
    width = 54
    title = "CLOUDCHAT"
    subtitle = "Cloudflare Workers AI  ·  Terminal chat"
    model = f"Model: {MODEL_NAME}"
    print()
    print("  ╭" + "─" * (width + 2) + "╮")
    for text, style in ((title, "1;36"), (subtitle, "90"), (model, "")):
        visible_text = _color(text, style, color) if style else text
        print(
            "  │ "
            + visible_text
            + " " * max(0, width - len(text))
            + " │"
        )
    print("  ╰" + "─" * (width + 2) + "╯")
    print(
        "  Comenzi: /help pentru ajutor · /clear conversația · /exit pentru ieșire"
    )
    print()


def _show_response(response, color):
    label = _color("AI", "1;32", color)
    print(f"\n  ╭─ {label} " + "─" * 58)
    for paragraph in response.splitlines() or [""]:
        if not paragraph:
            print("  │")
            continue
        wrapped = textwrap.wrap(
            paragraph,
            width=72,
            break_long_words=False,
            break_on_hyphens=False,
        ) or [paragraph]
        for line in wrapped:
            print(f"  │ {line}")
    print("  ╰" + "─" * 62)
    print()


def _show_help():
    print("\n  Scrie un mesaj și apasă Enter pentru a vorbi cu AI-ul.")
    print("  /clear  Începe o conversație nouă")
    print("  /help   Afișează acest ajutor")
    print("  /exit   Închide aplicația\n")


def main():
    logger = configure_logging()
    history = []
    color = _supports_color()
    _show_banner(color)

    while True:
        try:
            prompt = input(f"  {_color('Tu', '1;34', color)} › ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n  Conversație încheiată.\n")
            return 0

        if prompt.lower() in EXIT_COMMANDS:
            print("\n  Conversație încheiată.\n")
            return 0
        if prompt.lower() in HELP_COMMANDS:
            _show_help()
            continue
        if prompt.lower() in CLEAR_COMMANDS:
            history.clear()
            print("\n  Contextul conversației a fost resetat.\n")
            continue
        if not prompt:
            continue

        print(f"  {_color('AI', '90', color)} lucrează...")
        try:
            response = run_interaction(prompt, history=history, logger=logger)
        except Exception as error:
            print(
                f"\n  {_color('Eroare', '1;31', color)} "
                f"({type(error).__name__}). Detalii în jurnal.txt.\n"
            )
            continue

        _show_response(response, color)
        history.extend(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": response},
            ]
        )


if __name__ == "__main__":
    raise SystemExit(main())