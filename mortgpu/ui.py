"""Rich console helpers used by everyone (§6)."""
import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console(highlight=False)
SPEED = 0.0          # seconds of delay after each printed step (demo sets this)
PAUSE = False        # demo --pause: wait for Enter between scenes

STEP_STYLES = {
    "OBSERVE": "cyan", "PROPOSE": "magenta", "MEMORY": "yellow",
    "GATE": "white", "ACT": "blue", "AUDIT": "bright_black",
}


def _delay() -> None:
    if SPEED:
        time.sleep(SPEED)


def money(pence: int | None) -> str:
    if pence is None:
        return "—"
    sign = "-" if pence < 0 else ""
    return f"{sign}£{abs(pence) / 100:,.2f}"


def pct(permille: int | None) -> str:
    if permille is None:
        return "—"
    return f"{permille / 10:.1f}%"


def scene(n: int, title: str) -> None:
    console.print()
    console.rule(f"[bold red]Scene {n}[/] · [bold]{title}[/]", style="red")
    _delay()


def step(kind: str, text: str) -> None:
    style = STEP_STYLES.get(kind, "white")
    console.print(f"[bold {style}]{kind:<8}[/] {text}")
    _delay()


def gate(clauses) -> None:
    for c in clauses:
        mark, colour = ("✓", "green") if c.passed else ("✗", "red")
        console.print(f"[bold white]{'GATE':<8}[/] [{colour}]{mark} §{c.clause}[/] {c.detail}")
        _delay()


def verdict(decision) -> None:
    colour = "green" if decision.verdict == "ALLOW" else "red"
    console.print(Panel(f"[bold {colour}]{decision.verdict}[/]  {decision.id}  {decision.reason}",
                        border_style=colour, expand=False))
    _delay()


def audit(kind: str, amount_pence: int, clause: str | None, reason: str) -> None:
    amt = money(amount_pence) if amount_pence else ""
    cl = f"§{clause}" if clause else ""
    console.print(f"[bright_black]{'AUDIT':<8} {kind:<20} {amt:>12} {cl:<6} {reason}[/]")


def info(text: str) -> None:
    console.print(text)
    _delay()


def warn(text: str) -> None:
    console.print(f"[bold yellow]WARN[/] {text}")


def table(title: str, columns: list[str], rows: list[list]) -> None:
    t = Table(title=title, title_style="bold", border_style="bright_black")
    for c in columns:
        t.add_column(c)
    for r in rows:
        t.add_row(*[str(x) for x in r])
    console.print(t)
    _delay()


def pause() -> None:
    if PAUSE:
        console.input("[bright_black]⏎ Enter to continue[/]")
