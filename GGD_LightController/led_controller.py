from rpi_ws281x import PixelStrip, Color
import time
import threading

LED_COUNT = 112
LED_PIN = 18
LEDS_PER_NODE = 7
LED_FREQ_HZ = 800000
LED_DMA = 10
LED_BRIGHTNESS = 255
LED_INVERT = False
LED_CHANNEL = 0

strip = PixelStrip(LED_COUNT, LED_PIN, LED_FREQ_HZ, LED_DMA, LED_INVERT, LED_BRIGHTNESS, LED_CHANNEL)
strip.begin()

node_threads = {}

def hex_to_color(hex_str):
    hex_str = hex_str.lstrip("#")
    r, g, b = int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16)
    return Color(g, r, b)

def set_node_color(node_id, hex_color):
    start = node_id * LEDS_PER_NODE
    end = start + LEDS_PER_NODE
    color = hex_to_color(hex_color)
    for i in range(start, end):
        strip.setPixelColor(i, color)
    strip.show()

def flash_node(node_id, assigned_color_hex):
    start = node_id * LEDS_PER_NODE
    end = start + LEDS_PER_NODE
    green = Color(0, 255, 0)
    assigned_color = hex_to_color(assigned_color_hex)

    for _ in range(12):
        for i in range(start, end):
            strip.setPixelColor(i, green)
        strip.show()
        time.sleep(0.125)

        for i in range(start, end):
            strip.setPixelColor(i, assigned_color)
        strip.show()
        time.sleep(0.125)

    # Hold the final color
    set_node_color(node_id, assigned_color_hex)

def update_node_live_status(node_id, is_live, color_hex):
    def run():
        if is_live:
            flash_node(node_id, color_hex)
        else:
            set_node_color(node_id, "#000000")

    # Prevent overlap if a thread is already running for this node
    if node_id in node_threads and node_threads[node_id].is_alive():
        return

    t = threading.Thread(target=run)
    node_threads[node_id] = t
    t.start()
