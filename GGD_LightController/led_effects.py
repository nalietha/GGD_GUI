import time
from rpi_ws281x import Color
from colorsys import hsv_to_rgb


from led_utilities import transform_node_id, hex_to_color, strip, LEDS_PER_NODE, get_node_range, LED_COUNT

# === Simple Effects ===

def solid(node_id, color_hex):
    color = hex_to_color(color_hex)
    for i in get_node_range(node_id):
        strip.setPixelColor(i, color)
    strip.show()

def blink(node_id, color_hex, duration=1.0, repeat=3):
    color = hex_to_color(color_hex)
    for _ in range(repeat):
        for i in get_node_range(node_id):
            strip.setPixelColor(i, color)
        strip.show()
        time.sleep(duration / 2)
        for i in get_node_range(node_id):
            strip.setPixelColor(i, 0)
        strip.show()
        time.sleep(duration / 2)

# === Complex Effects ===
def apply_static_gradient(brightness=0.2):
    print("Applying static gradient across nodes...")

    for node_id in range(16):
        hue = node_id / 16.0
        r, g, b = hsv_to_rgb(hue, 1.0, 0.2)
        color = Color(int(r * 255), int(g * 255), int(b * 255))
        start = transform_node_id(node_id) * LEDS_PER_NODE
        for i in range(LEDS_PER_NODE):
            strip.setPixelColor(start + i, color)
    strip.show()


def pulsate(node_id, color1_hex, color2_hex, speed=0.15, cycles=5):
    c1 = hex_to_color(color1_hex)
    c2 = hex_to_color(color2_hex)
    r = get_node_range(node_id)
    for _ in range(cycles):
        for i in r:
            strip.setPixelColor(i, c1)
        strip.show()
        time.sleep(speed)
        for i in r:
            strip.setPixelColor(i, c2)
        strip.show()
        time.sleep(speed)


def rainbow(node_id, wait=0.05, cycles=1):
    r = get_node_range(node_id)
    for j in range(256 * cycles):
        for i, led_index in enumerate(r):
            color = wheel((i * 256 // LEDS_PER_NODE + j) & 255)
            strip.setPixelColor(led_index, color)
        strip.show()
        time.sleep(wait)


def sparkle(node_id, base_color_hex="#000000", sparkle_color_hex="#FFFFFF", count=5, delay=0.1):
    r = get_node_range(node_id)
    base = hex_to_color(base_color_hex)
    sparkle = hex_to_color(sparkle_color_hex)
    import random

    for i in r:
        strip.setPixelColor(i, base)
    strip.show()

    for _ in range(count):
        idx = random.choice(list(r))
        strip.setPixelColor(idx, sparkle)
        strip.show()
        time.sleep(delay)
        strip.setPixelColor(idx, base)
        strip.show()


def pulse_node(node_id, color_hex, cycles=3, speed=0.1):
    color = hex_to_color(color_hex)
    physical = transform_node_id(node_id)
    start = physical * LEDS_PER_NODE

    for _ in range(cycles):
        for brightness in range(0, 256, 25):
            faded = Color((color >> 16 & 0xff) * brightness // 255,
                          (color >> 8 & 0xff) * brightness // 255,
                          (color & 0xff) * brightness // 255)
            for i in range(start, start + LEDS_PER_NODE):
                strip.setPixelColor(i, faded)
            strip.show()
            time.sleep(speed)
        for brightness in range(255, -1, -25):
            faded = Color((color >> 16 & 0xff) * brightness // 255,
                          (color >> 8 & 0xff) * brightness // 255,
                          (color & 0xff) * brightness // 255)
            for i in range(start, start + LEDS_PER_NODE):
                strip.setPixelColor(i, faded)
            strip.show()
            time.sleep(speed)


def flash_node(node_id, color_hex):
    green = Color(0, 255, 0)
    user_color = hex_to_color(color_hex)
    physical = transform_node_id(node_id)
    start = physical * LEDS_PER_NODE
    for _ in range(12):
        for i in range(start, start + LEDS_PER_NODE):
            strip.setPixelColor(i, green)
        strip.show()
        time.sleep(0.125)
        for i in range(start, start + LEDS_PER_NODE):
            strip.setPixelColor(i, user_color)
        strip.show()
        time.sleep(0.125)
    for i in range(start, start + LEDS_PER_NODE):
        strip.setPixelColor(i, user_color)
    strip.show()


def startup_chase_effect(colors=["#ff0000", "#00ff00", "#0000ff"], rounds=3, delay=0.1):
    def animate():
        print("Running startup animation...")

        node_count = 16
        sequence = list(range(node_count))  # Logical order

        for r in range(rounds):
            for step in range(node_count):
                for i, node_id in enumerate(sequence):
                    color = colors[(step + i) % len(colors)]
                    set_node_color(node_id, color)
                time.sleep(delay)

        # Clear after final loop
        for node_id in range(node_count):
            set_node_color(node_id, "#000000")

        print("Startup animation complete.")

    Thread(target=animate, daemon=True).start()


# === Helpers ===
def set_node_color(node_id, color_hex):
    physical = transform_node_id(node_id)
    start = physical * LEDS_PER_NODE
    color = hex_to_color(color_hex)
    for i in range(LEDS_PER_NODE):
        strip.setPixelColor(start + i, color)
    strip.show()

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


def wheel(pos):
    """Generate rainbow colors across 0-255 positions."""
    if pos < 85:
        return Color(pos * 3, 255 - pos * 3, 0)
    elif pos < 170:
        pos -= 85
        return Color(255 - pos * 3, 0, pos * 3)
    else:
        pos -= 170
        return Color(0, pos * 3, 255 - pos * 3)
