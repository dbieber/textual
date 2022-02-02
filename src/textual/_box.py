from __future__ import annotations

from typing import Literal

from rich.console import Console, ConsoleOptions, RenderResult, RenderableType
from rich.segment import Segment

BOX_STYLES: dict[str, tuple[str, str, str]] = {
    "": ("   ", "   ", "   "),
    "rounded": ("╭─╮", "│ │", "╰─╯"),
    "solid": ("┌─┐", "│ │", "└─┘"),
    "double": ("╔═╗", "║ ║", "╚═╝"),
    "dashed": ("┏╍┓", "╏ ╏", "┗╍┛"),
    "heavy": ("┏━┓", "┃ ┃", "┗━┛"),
    "inner": ("▗▄▖", "▐ ▌", "▝▀▘"),
    "outer": ("▛▀▜", "▌ ▐", "▙▄▟"),
}

BoxType = Literal["", "rounded", "solid", "double", "dashed", "heavy", "inner", "outer"]


class Box:
    def __init__(
        self,
        renderable: RenderableType,
        *,
        sides: tuple[str, str, str, str],
        styles: tuple[str, str, str, str],
    ):
        self.renderable = renderable
        self.sides = sides
        self.styles = styles

    def __rich_console__(
        self, console: "Console", options: "ConsoleOptions"
    ) -> "RenderResult":
        width = options.max_width

        top, right, bottom, left = (
            side if side != "none" else "" for side in self.sides
        )
        top_style, right_style, bottom_style, left_style = map(
            console.get_style, self.styles
        )

        BOX = BOX_STYLES
        renderable = self.renderable
        render_width = width - bool(left) - bool(right)
        lines = console.render_lines(renderable, options.update_width(render_width))

        new_line = Segment.line()

        if top != "none":
            char_left, char_mid, char_right = iter(BOX[top][0])
            row = f"{char_left if left else ''}{char_mid * render_width}{char_right if right else ''}"
            yield Segment(row, top_style)
            yield new_line

        if not left and not right:
            for line in lines:
                yield from line
                yield new_line
        elif left and right:
            left_segment = Segment(BOX[left][1][0], left_style)
            right_segment = Segment(BOX[right][1][2] + "\n", right_style)
            for line in lines:
                yield left_segment
                yield from line
                yield right_segment
        elif left:
            left_segment = Segment(BOX[left][1][0], left_style)
            for line in lines:
                yield left_segment
                yield from line
                yield new_line
        elif right:
            right_segment = Segment(BOX[right][1][2] + "\n", right_style)
            for line in lines:
                yield from line
                yield right_segment

        if bottom:
            char_left, char_mid, char_right = iter(BOX[bottom][2])
            row = f"{char_left if left else ''}{char_mid * render_width}{char_right if right else ''}"
            yield Segment(row, bottom_style)
            yield new_line


if __name__ == "__main__":
    from rich import print

    box = Box(
        "foo",
        sides=("rounded", "rounded", "rounded", "rounded"),
        styles=("green", "green", "green", "on green"),
    )
    print(box)