using System.Text.Json.Serialization;

namespace GGD_Display
{
    public class DisplayModels
    {
        public class Metadata
        {
            public string Version { get; set; } = "0.0.0.3a";
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
            public string Name { get; set; } = "";
            public string StreamerId { get; set; } = "";
            public string StreamerColor { get; set; } = "#FFFFFF";
            public bool IsLive { get; set; } = false;
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
    }
}
