from rpi_ws281x import PixelStrip, Color
from config_manager import read_brightness

# LED config
LED_COUNT = 112
LEDS_PER_NODE = 7
LED_PIN = 18
LED_FREQ_HZ = 800000
LED_DMA = 10
LED_INVERT = False
LED_CHANNEL = 0

LED_BRIGHTNESS = read_brightness("appsettings.json")

strip = PixelStrip(LED_COUNT, LED_PIN, LED_FREQ_HZ, LED_DMA, LED_INVERT, LED_BRIGHTNESS, LED_CHANNEL)
strip.begin()
print(f"[startup] Brightness set to {LED_BRIGHTNESS}")


def hex_to_color(hex_string):
    hex_string = hex_string.lstrip('#')
    return Color(int(hex_string[0:2], 16), int(hex_string[2:4], 16), int(hex_string[4:6], 16))

def get_node_range(node_id):
    start = transform_node_id(node_id) * LEDS_PER_NODE
    return range(start, start + LEDS_PER_NODE)

def transform_node_id(x):
    return 15 - x if x < 8 else x - 8

