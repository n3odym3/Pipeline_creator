from modules.basic_ui.text_viewer_win import Text_viewer_win
from modules.basic_ui.Hello_world_win import HelloWorld_win

def create_windows() -> None:
    helloworld = HelloWorld_win(label="Hello World", pos=(50, 50), win_width=300, win_height=200)
    text_viewer = Text_viewer_win(label="Text Viewer", pos=(350, 50), win_width=400, win_height=300)

    helloworld.connect_to(text_viewer, 0)
