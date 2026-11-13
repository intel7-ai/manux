"""Original app lookup; actual launch is injected by desktop.py."""
from manux.platform.custom_app_lookup import Target, resolve, _split, find_by_name, _is_program_folder
def start(target):
    from manux.compat.hand_mouse import ports
    return ports.launch(target)
