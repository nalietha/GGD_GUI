from twitch_api_service import TwitchAPIService
from save_manager import load_settings, save_settings

def update_streamer_status(twitch: TwitchAPIService):
    settings = load_settings()

    ids = [s["publicStreamerId"] for s in settings["streamers"] if s.get("publicStreamerId")]
    if not ids:
        return

    live_status = twitch.get_live_status(ids)

    for streamer in settings["streamers"]:
        sid = streamer.get("publicStreamerId")
        if sid:
            streamer["iIsLive"] = live_status.get(sid, False)

    save_settings(settings)

def parse_streamer_name(input_str):
    input_str = input_str.strip().lower()
    if "twitch.tv/" in input_str:
        return input_str.split("twitch.tv/")[-1].split("/")[0]
    return input_str

