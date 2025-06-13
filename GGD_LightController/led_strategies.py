from led_effects import set_node_color, flash_node, apply_display_effect, apply_static_gradient
from led_utilities import strip, LEDS_PER_NODE, hex_to_color


class StreamerModeStrategy:
    def __init__(self, twitch, previous_live, current_colors, current_mode):
        self.twitch = twitch
        self.previous_live = previous_live
        self.current_colors = current_colors
        self.current_mode = current_mode


    def update(self, assignments):
        public_ids = {node["publicId"] for nodes in assignments.values() for node in nodes}
        live_map = self.twitch.get_live_status(list(public_ids))

        any_live = any(live_map.get(node["publicId"], False) for nodes in assignments.values() for node in nodes)

        # If no one is live, apply ambient
        if not any_live:
            if self.current_mode["value"] != "gradient":
                print("Switching to static gradient")
                apply_static_gradient()
                self.current_mode["value"] = "gradient"
            return

        # Someone IS live
        if self.current_mode["value"] != "streamer":
            print("Streamers live — restoring control")
            for i in range(16):
                set_node_color(i, "#000000")  # Clear LEDs
            self.current_mode["value"] = "streamer"

        # Proceed with streamer updates
        for sid, nodes in assignments.items():
            is_live = live_map.get(nodes[0]["publicId"], False)
            prev = self.previous_live.get(sid)
            self.previous_live[sid] = is_live

            for node in nodes:
                node_id = node["nodeId"]
                final_color = node.get("streamerColor", "#000000").strip() if is_live else "#000000"

                if prev is None and is_live:
                    flash_node(node_id, final_color)
                    set_node_color(node_id, final_color)
                elif not prev and is_live:
                    flash_node(node_id, final_color)
                    set_node_color(node_id, final_color)
                elif is_live:
                    if self.current_colors.get(node_id) != final_color:
                        set_node_color(node_id, final_color)
                else:
                    if self.current_colors.get(node_id) != "#000000":
                        set_node_color(node_id, "#000000")

                self.current_colors[node_id] = final_color

    



class DisplayEffectStrategy:
    def update(self, assignments):
        for sid, nodes in assignments.items():
            for node in nodes:
                node_id = node["nodeId"]
                color = node.get("ColorHex", "#000000").strip()
                setting = node.get("DisplaySetting", "static").strip().lower()
                apply_display_effect(node_id, setting, color)
