# Managing Tutorials & Programmatic Hints

As a developer building new modules, you can integrate with Pipeline Creator's **Tutorial Manager** in two ways:

1. **Module-Specific Tutorials (`tutorial.json`)**: Auto-bind a pre-recorded walkthrough that users can trigger via the Node Editor.
2. **Programmatic API Invocations**: Trigger real-time, context-aware highlights, errors, or alerts directly from your python module code.

You can programmatically trigger the Tutorial Manager to show hints, alerts, or enforce workflow rules based on live status or the current privilege mode (such as blocking a `"user"` action until they perform a prerequisite step):

```python
from core.tutorial_manager import tutorial_manager
from core.app_state import app_state

# Example 1: Enforce a workflow rule in User Mode by blocking an action
# and automatically showing a tutorial reminder pointing to the required widget
def on_export_clicked(self):
    if app_state.mode == "user" and not self.sample_name:
        # Highlight the input field and display a helper alert
        tutorial_manager.play_error_hint(
            item_tag=self.sample_name_input_tag, 
            instruction="Workflow reminder: You must enter a Sample name/ID first."
        )
        return  # Block execution
        
    # Continue with action...

# Example 2: Highlight a widget with a success indicator
tutorial_manager.play_success_hint(
    item_tag=f"run_btn_{self.UUID}", 
    instruction="Calibration succeeded! Click here to start."
)

# Example 3: Display a central alert message on screen
tutorial_manager.play_center_hint(
    instruction="Connection lost. Reconnecting...", 
    type="error_hint", 
    icon_type="error"
)
```
