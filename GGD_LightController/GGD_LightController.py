from signalrcore.hub_connection_builder import HubConnectionBuilder
from rpi_ws281x import PixelStrip, Color
import json
import time

# LED configuration
LED_COUNT = 112
LEDS_PER_NODE = 7
LED_PIN = 18
LED_FREQ_HZ = 800000
LED_DMA = 10
LED_BRIGHTNESS = 64
LED_INVERT = False
LED_CHANNEL = 0

strip = PixelStrip(LED_COUNT, LED_PIN, LED_FREQ_HZ, LED_DMA, LED_INVERT, LED_BRIGHTNESS, LED_CHANNEL)
strip.begin()

# Maps privateId to nodeId and color
node_assignments = {}  # filled from save.json

def hex_to_color(hex_string):
    hex_string = hex_string.lstrip('#')
    return Color(int(hex_string[0:2], 16), int(hex_string[2:4], 16), int(hex_string[4:6], 16))

def set_node_color(node_id, color):
    start = node_id * LEDS_PER_NODE
    for i in range(LEDS_PER_NODE):
        strip.setPixelColor(start + i, color)
    strip.show()

def flash_node(node_id, color_hex):
    green = Color(0, 255, 0)
    user_color = hex_to_color(color_hex)
    for _ in range(12):
        set_node_color(node_id, green)
        time.sleep(0.125)
        set_node_color(node_id, user_color)
        time.sleep(0.125)
    set_node_color(node_id, user_color)

def handle_streamer_update(args):
    data = args[0]
    sid = data['privateId']
    is_live = data.get('isLive', False)
    color_hex = data.get('streamerColor', '#000000')

    node = node_assignments.get(sid)
    if node is None:
        print(f"Unknown streamer ID: {sid}")
        return

    print(f"Streamer update: Node {node['nodeId']}, Live: {is_live}")
    if is_live:
        flash_node(node['nodeId'], color_hex)
    else:
        set_node_color(node['nodeId'], hex_to_color(color_hex))

def load_node_assignments(path='save.json'):
    with open(path, 'r') as f:
        settings = json.load(f)
        result = {}
        for node in settings['nodes']:
            sid = node.get('linkedStreamerId')
            if sid:
                result[sid] = {
                    'nodeId': node['nodeId'],
                    'color': node.get('colorHex', '#000000')
                }
        return result

if __name__ == "__main__":
    node_assignments = load_node_assignments()

    hub = HubConnectionBuilder()\
        .with_url("http://localhost:5000/twitchhub")\
        .build()

    hub.on("updateStreamer", handle_streamer_update)

    hub.start()
    print("SignalR connection started. Waiting for streamer updates...")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down...")
        hub.stop()
        for i in range(LED_COUNT):
            strip.setPixelColor(i, Color(0, 0, 0))
        strip.show()
