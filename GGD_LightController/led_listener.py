import time
import json
from TwitchAPI.twitch_api_service import TwitchAPIService
from led_effects import pulse_mode, flash_node
from rpi_ws281x import PixelStrip, Color
from led_strategies import StreamerModeStrategy, DisplayEffectStrategy
from led_utilities import transform_node_id, hex_to_color, strip, LEDS_PER_NODE


previous_live = {}  # sid -> bool
SAVE_PATH = "/var/www/ggd_display/wwwroot/data/save.json"


def set_node_color(node_id, color_hex):
    physical = transform_node_id(node_id)
    start = physical * LEDS_PER_NODE
    color = hex_to_color(color_hex)
    for i in range(LEDS_PER_NODE):
        strip.setPixelColor(start + i, color)
    strip.show()



def load_assignments(path=SAVE_PATH):
    with open(path, "r") as f:
        data = json.load(f)

    node_map = {}
    streamer_map = {}

    for s in data.get("streamers", []):
        sid = s["privateId"]
        streamer_map[sid] = {
            "public": s["publicStreamerId"],
            "color": s.get("streamerColor", "#000000"),
            "alt": s.get("altColorHex")
        }

    for node in data.get("nodes", []):
        sid = node.get("LinkedStreamerId")
        if not sid or sid not in streamer_map:
            continue
        entry = {
            "nodeId": node["NodeId"],
            "color": node.get("ColorHex", "#000000"),
            "streamerColor": streamer_map[sid]["color"],
            "altColor": streamer_map[sid]["alt"],
            "publicId": streamer_map[sid]["public"]
        }
        node_map.setdefault(sid, []).append(entry)

    return node_map

def read_ids_from_appsettings(filepath):
    """Reads IDs from appsettings.json file.

    Args:
        filepath (str): Path to appsettings.json file.

    Returns:
        dict: A dictionary of IDs, or None if an error occurs.
    """
    try:
        with open(filepath, 'r') as f:
            appsettings = json.load(f)
    except FileNotFoundError:
        print(f"Error: File not found at {filepath}")
        return None
    except json.JSONDecodeError:
         print(f"Error: Invalid JSON format in {filepath}")
         return None
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return None

    # Example: Assuming IDs are under "Twitch"
    try:
        ids = appsettings["Twitch"]
        return ids
    except KeyError:
        print("Error: 'Twitch' key not found in appsettings.json.")
        return None


def resolve_node_color(node, is_live):
    color = node.get("streamerColor", "#000000").strip()
    return color if is_live else "#000000"


def get_settings(key, term, filepath='appsettings.json'):
    try:
        with open(filepath, 'r') as f:
            appsettings = json.load(f)
    except FileNotFoundError:
        print(f"Error: File not found at {filepath}")
        return None
    except json.JSONDecodeError:
         print(f"Error: Invalid JSON format in {filepath}")
         return None
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return None
    
    # get the term by the key
    try:
        ids = appsettings[key]
        return ids[term]
    except KeyError:
        print("Error: '{key}:{term}' key not found in {filepath}.")
        return None
#region Content Checks
def is_streamer_mode_enabled(path="appsettings.json"):
    try:
        with open(path, "r") as f:
            data = json.load(f)
            return data.get("Mode", {}).get("StreamerEnabled", True)
    except Exception as e:
        print(f"Error reading appsettings.json: {e}")
        return True

def is_adult_check_enabled(path="appsettings.json"):
    try:
        with open(path, "r") as f:
            data = json.load(f)
            return data.get("Mode", {}).get("AdultContentCheckEnabled", False)
    except Exception as e:
        print(f"Error reading appsettings.json: {e}")
        return False
#endregion 



def run_poll_loop():
    streamer_mode = is_streamer_mode_enabled()
    config = AppSettingsManager()
    if is_adult_check_enabled():
        print("Adult content checking is enabled.")
        # TODO: call external APIs here
    else:
        print("Adult content check is disabled.")

    previous_live = {}
    current_colors = {}

    if streamer_mode:
        clientIDs = read_ids_from_appsettings("appsettings.json")
        twitch = TwitchAPIService(clientIDs["ClientId"], clientIDs["ClientSecret"])
        twitch.initialize()
        strategy = StreamerModeStrategy(twitch, previous_live, current_colors)
        print("Streamer mode active.")
    else:
        strategy = DisplayEffectStrategy()
        print("Streamer mode disabled. Running visual effects only.")

    while True:
        # checks for any changes in the settings
        streamer_mode = config.get("Mode.StreamerEnabled", True)
        adult_check = config.get("Mode.AdultContentCheckEnabled", False)


        assignments = load_assignments()
        strategy.update(assignments)
        time.sleep(10)


def apply_display_effect(node_id, setting, color_hex):
    setting = setting.lower()
    if setting == "static":
        set_node_color(node_id, color_hex)
    elif setting == "pulse":
        pulse_node(node_id, color_hex)
    elif setting == "off":
        set_node_color(node_id, "#000000")
    else:
        print(f"Unknown display setting '{setting}' on node {node_id}, defaulting to static")
        set_node_color(node_id, color_hex)


if __name__ == "__main__":
    try:
        run_poll_loop()
    except KeyboardInterrupt:
        print("Stopping. Clearing LEDs...")
        for i in range(LED_COUNT):
            strip.setPixelColor(i, Color(0, 0, 0))
        strip.show()

