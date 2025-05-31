import requests

class TwitchAPIService:
    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = None

    def initialize(self):
        url = "https://id.twitch.tv/oauth2/token"
        params = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials"
        }
        response = requests.post(url, params=params)
        response.raise_for_status()
        self.access_token = response.json()["access_token"]

    def _get_headers(self):
        if not self.access_token:
            raise RuntimeError("Twitch API not initialized")
        return {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.access_token}"
        }

    def get_live_status(self, public_ids):
        def fetch_chunk(chunk):
            query = "&".join(f"user_id={id_}" for id_ in chunk)
            url = f"https://api.twitch.tv/helix/streams?{query}"
            response = requests.get(url, headers=self._get_headers())

            if response.status_code == 401:
                print("Access token expired — refreshing...")
                self.initialize()
                response = requests.get(url, headers=self._get_headers())

            response.raise_for_status()
            return response.json()

        live_status = {id_: False for id_ in public_ids}

        for chunk in [public_ids[i:i+100] for i in range(0, len(public_ids), 100)]:
            data = fetch_chunk(chunk)
            for stream in data.get("data", []):
                uid = stream["user_id"]
                live_status[uid] = True

        return live_status


