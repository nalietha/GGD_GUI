using Microsoft.AspNetCore.Components.Web.Virtualization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.AspNetCore.SignalR;
using System.Text.Json;
using GGD_Display.Models;
using GGDTwitchAPI;
using System.Diagnostics;
using System.Text.Json.Serialization;
using static GGD_Display.Utilities;



namespace GGD_Display.Pages
{
    public class IndexModel : PageModel
    {
        private readonly IHubContext<TwitchHub> _hub;
        private readonly TwitchApiService _twitch;
        private readonly ILogger<RefreshTwitchStatusService> _logger;
        public IndexModel(
            IHubContext<TwitchHub> hub,
            TwitchApiService twitch,
            ILogger<RefreshTwitchStatusService> logger)
        {
            _hub = hub;
            _twitch = twitch;
            _logger = logger;
        }

        public List<NodeInfo> Canvases { get; set; } = new(); // for UI use
        public List<StreamerInfo> Streamers { get; set; } = new();

        public List<LightingMode> LightingModes { get; set; } = new()
        {
            new LightingMode { Name = "Pulse", Icon = "💡" },
            new LightingMode { Name = "Wave", Icon = "🌊" },
            new LightingMode { Name = "Static", Icon = "🔒" },
            new LightingMode { Name = "Rainbow", Icon = "🌈" },
            new LightingMode { Name = "Flash", Icon = "⚡" },
        };

        public void OnGet()
        {
            var settings = FileController.LoadSettings();

            Canvases = settings.Canvases ?? new List<NodeInfo>();
            Streamers = settings.Streamers ?? new List<StreamerInfo>();

        }
        public class UpdateNodeRequest
        {
            [JsonPropertyName("nodeId")]
            public int NodeId { get; set; }
            [JsonPropertyName("privateId")]
            public string PrivateId { get; set; }
        }

        public class UpdateStreamerLinkRequest
        {
            [JsonPropertyName("nodeId")]
            public int NodeId { get; set; }
            [JsonPropertyName("privateId")]
            public string PrivateId { get; set; }
        }

        public async Task<IActionResult> OnPostUpdateNodeStreamerAsync()
        {
            var body = await new StreamReader(Request.Body).ReadToEndAsync();
            var update = JsonSerializer.Deserialize<UpdateStreamerLinkRequest>(body, new JsonSerializerOptions
            {
                PropertyNameCaseInsensitive = true
            });

            if (update == null || string.IsNullOrWhiteSpace(update.PrivateId))
                return new JsonResult(new { success = false, message = "Invalid input" });

            var settings = FileController.LoadSettings();

            var node = settings.Canvases.FirstOrDefault(n => n.NodeId == update.NodeId);
            var streamer = settings.Streamers.FirstOrDefault(s => s.PrivateId == update.PrivateId);

            if (node == null || streamer == null)
                return new JsonResult(new { success = false, message = "Node or streamer not found" });

            node.LinkedStreamerId = update.PrivateId;
            FileController.SaveSettings(settings);

            var colorHex = streamer.IsLive ? streamer.AltColorHex ?? streamer.StreamerColor : streamer.StreamerColor;

            return new JsonResult(new
            {
                success = true,
                colorHex,
                streamerName = streamer.Name,
                isLive = streamer.IsLive
            });
        }
        public class UpdateNodeColorRequest
        {
            [JsonPropertyName("nodeId")]
            public int NodeId { get; set; }

            [JsonPropertyName("colorHex")]
            public string ColorHex { get; set; } = "";

            [JsonPropertyName("isAlt")]
            public bool IsAlt { get; set; }
        }

        public async Task<IActionResult> OnPostUpdateNodeColorAsync([FromBody] UpdateNodeColorRequest request)
        {
            var settings = FileController.LoadSettings();

            var node = settings.Canvases.FirstOrDefault(n => n.NodeId == request.NodeId);
            if (node == null)
                return new JsonResult(new { success = false, message = "Node not found" });

            // Update node color only if it's a main color update
            if (!request.IsAlt)
                node.ColorHex = request.ColorHex;

            if (!string.IsNullOrEmpty(node.LinkedStreamerId))
            {
                var streamer = settings.Streamers.FirstOrDefault(s => s.PrivateId == node.LinkedStreamerId);
                if (streamer != null)
                {
                    if (request.IsAlt)
                        streamer.AltColorHex = request.ColorHex;
                    else
                        streamer.StreamerColor = request.ColorHex;
                }
            }

            FileController.SaveSettings(settings);

            return new JsonResult(new { success = true });
        }

        public async Task<IActionResult> OnPostRefreshIdsAsync()
        {
            int updated = await AdvancedSaveUtils.RefreshStreamerIdsFromNamesAsync(_twitch); // Use your service
            return new JsonResult(new { success = true, updated });
        }

        public string GetStreamerNameForNode(int nodeId)
        {
            var node = Canvases.FirstOrDefault(n => n.NodeId == nodeId);
            var streamer = Streamers.FirstOrDefault(s => s.PublicStreamerId == node?.LinkedStreamerId);
            return streamer?.Name ?? $"Node {nodeId}";
        }



        /// <summary>
        /// Node ID normalization for display purposes. So that nodes start at top right and bottom right 
        /// Not sure this is needed as long as IDs are consistent in the JSON file.
        /// </summary>
        /// <param name="nodeId"></param>
        /// <returns></returns>
        public int reviseNodeOrder(int nodeId)
        {
            return nodeId < 8 ? 15 - nodeId : nodeId - 8;
        }

    }

}
