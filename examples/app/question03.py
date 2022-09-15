from textual.app import App, ComposeResult
from textual.widgets import Static, Button


class QuestionApp(App[str]):
    CSS = """
    Screen {
        layout: table;
        table-size: 2;
        table-gutter: 2; 
        padding: 2;   
    }
    #question {
        width: 100%;
        height: 100%;
        column-span: 2;
        content-align: center bottom;
        text-style: bold;
    } 

    Button {
        width: 100%;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Do you love Textual?", id="question")
        yield Button("Yes", id="yes", variant="primary")
        yield Button("No", id="no", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.exit(event.button.id)


app = QuestionApp()
if __name__ == "__main__":
    reply = app.run()
    print(reply)