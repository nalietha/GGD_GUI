using System.Text.Json.Serialization;


namespace GGD_Display.Models
{

    public class Metadata
    {
        public string? Version { get; set; }
        public DateTime LastUpdated { get; set; } = DateTime.UtcNow;
    }

    public class NodeInfo
    {
        public int NodeId { get; set; }
        public string ColorHex { get; set; } = "#9146FF"; // twitch purple
        public string LinkedStreamerId { get; set; } = "";
        public string DisplaySetting { get; set; } = "static";
    }

    public class StreamerInfo
    {
        [JsonPropertyName("privateId")]
        public string PrivateId { get; set; }

        [JsonPropertyName("publicStreamerId")]
        public string PublicStreamerId { get; set; }

        [JsonPropertyName("name")]
        public string Name { get; set; }

        [JsonPropertyName("platform")]
        public string Platform { get; set; }

        [JsonPropertyName("streamerColor")]
        public string StreamerColor { get; set; }

        [JsonPropertyName("altColorHex")]
        public string AltColorHex { get; set; }

        [JsonPropertyName("isLive")]
        public bool IsLive { get; set; }
    }

    public class AppSettings
    {
        [JsonPropertyName("metadata")]
        public Metadata Metadata { get; set; } = new();

        [JsonPropertyName("nodes")]
        public List<NodeInfo> Canvases { get; set; } = new(); // UI still uses "canvases"

        [JsonPropertyName("streamers")]
        public List<StreamerInfo> Streamers { get; set; } = new();
    }

    public class LightingMode
    {
        public string Name { get; set; }
        public string Icon { get; set; } // Ideally an emoji or short text representation
    }

    public class Settings
    {
        public string DisplayMode { get; set; } = "static"; // Default display mode
        public string Brightness { get; set; } = "100"; // Default brightness

    }
    public class StreamerInputInfo
    {
        public string Username { get; set; }
        public string Platform { get; set; }
    }

}
