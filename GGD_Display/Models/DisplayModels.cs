using System.Text.Json.Serialization;


namespace GGD_Display.Models
{

    public class Metadata
    {
        public string? Version { get; set; } = Utilities.GetAppVersion();
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
        [JsonPropertyName("displayName")]
        public string DisplayName { get; set; }

        [JsonPropertyName("platform")]
        public string Platform { get; set; }

        [JsonPropertyName("streamerColor")]
        public string StreamerColor { get; set; }

        [JsonPropertyName("altColorHex")]
        public string AltColorHex { get; set; }

        [JsonPropertyName("isLive")]
        public bool IsLive { get; set; }
    }

    public class AppSaveData
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

    public class StreamerInputInfo
    {
        public string Username { get; set; }
        public string Platform { get; set; }
    }

    // This class is used to store application settings in appsettings.json
    public class AppSettings
    {
        public Metadata Metadata { get; set; } = new Metadata();
        public bool StreamerModeEnabled { get; set; }
        public bool AdultContentCheckEnabled { get; set; }
        public string Mode { get; set; } = "static"; 
        public int Brightness { get; set; } = 128; // Default brightness level
        public AdultContentSettings? AdultContent { get; set; }
    }

    public class GGDDisplaySettings
    {
        public bool StreamerModeEnabled { get; set; } = false;
        public bool AdultContentCheckEnabled { get; set; } = false;
        public string Mode { get; set; } = "static";

        public int Brightness { get; set; } = 128;
        public AdultContentSettings? AdultContent { get; set; }

    }


    public class AdultContentSettings
    {
        public List<string> SupportedPlatforms { get; set; } = new();
        public Dictionary<string, string> ApiKeys { get; set; } = new();
    }
    public class AdultPlatformPlugin
    {
        public string Platform { get; set; } = "";
        public string ApiUrl { get; set; } = "";
        public bool RequiresApiKey { get; set; }
    }
}
