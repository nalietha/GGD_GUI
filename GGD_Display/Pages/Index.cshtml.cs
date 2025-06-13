using GGD_Display.Models;
using GGDTwitchAPI;
using Microsoft.AspNetCore.Components.Web.Virtualization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.AspNetCore.SignalR;
using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Xml.Linq;
using static GGD_Display.Utilities;



namespace GGD_Display.Pages
{
    public class IndexModel : PageModel
    {
        private readonly IHubContext<TwitchHub> _hub;
        private readonly TwitchApiService _twitch;
        private readonly ILogger<RefreshTwitchStatusService> _logger;
        private readonly IWebHostEnvironment _env;



        public IndexModel(
            IHubContext<TwitchHub> hub,
            TwitchApiService twitch,
            ILogger<RefreshTwitchStatusService> logger,
            IWebHostEnvironment env)
        {
            _hub = hub;
            _twitch = twitch;
            _logger = logger;
            _env = env;
        }

        public List<NodeInfo> Canvases { get; set; } = new(); // for UI use
        public List<StreamerInfo> Streamers { get; set; } = new();

        public List<LightingMode> LightingModes { get; set; } = new()
        {
            new LightingMode { Name = "Static", Icon = "🔒" },
            new LightingMode { Name = "Pulse", Icon = "💡" },
            new LightingMode { Name = "Wave", Icon = "🌊" },
            new LightingMode { Name = "Rainbow", Icon = "🌈" },
            new LightingMode { Name = "Flash", Icon = "⚡" },
        };

        public IActionResult OnGet()
        {
            var settings = FileController.LoadSaveData();

            Canvases = new List<NodeInfo>(); // loads them below
            Streamers = settings.Streamers ?? new List<StreamerInfo>();

            var savePath = Path.Combine(_env.WebRootPath, "data", "save.json");
            if (!System.IO.File.Exists(savePath))
                return NotFound();

            var json = System.IO.File.ReadAllText(savePath);
            var parsed = JsonDocument.Parse(json);
            var nodeArray = parsed.RootElement.GetProperty("nodes");

            foreach (var n in nodeArray.EnumerateArray())
            {
                Canvases.Add(new NodeInfo
                {
                    NodeId = n.GetProperty("NodeId").GetInt32(),
                    ColorHex = n.TryGetProperty("ColorHex", out var colorProp) ? colorProp.GetString() ?? "#9146FF" : "#9146FF",
                    LinkedStreamerId = n.TryGetProperty("LinkedStreamerId", out var linkProp) ? linkProp.GetString() ?? "" : "",
                    DisplaySetting = n.TryGetProperty("DisplaySetting", out var dispProp) ? dispProp.GetString() ?? "static" : "static"
                });
            }

            ViewData["PreviewGridHtml"] = RenderPreviewGridFromCanvases();

            return Page();
        }

        private string RenderPreviewGridFromCanvases()
        {
            // Create a lookup by NodeId for safety
            var canvasMap = Canvases.ToDictionary(c => c.NodeId);

            var sb = new StringBuilder();
            sb.AppendLine("<div class=\"grid grid-cols-8 gap-2\">");

            for (int nodeId = 0; nodeId < 16; nodeId++)
            {
                if (!canvasMap.TryGetValue(nodeId, out var canvas))
                {
                    sb.AppendLine($"<div class='w-10 h-10 border bg-gray-300 text-xs flex items-center justify-center'>N/A</div>");
                    continue;
                }

                var streamer = Streamers.FirstOrDefault(s => s.PrivateId == canvas.LinkedStreamerId);
                var color = !string.IsNullOrWhiteSpace(streamer?.StreamerColor) ? streamer.StreamerColor : "#333333";

                var setting = string.IsNullOrWhiteSpace(canvas.DisplaySetting) ? "static" : canvas.DisplaySetting.ToLower();
                var effectClass = setting switch
                {
                    "pulse" => "pulse",
                    "blink" => "blink",
                    "off" => "off",
                    _ => "static"
                };

                sb.AppendLine($@"
                    <div class='w-10 h-10 rounded flex items-center justify-center text-xs font-bold border {effectClass}'
                         style='background-color:{color}'
                         title='Node {canvas.NodeId+1} - {setting}'>
                         {canvas.NodeId+1}
                    </div>");
            }

            sb.AppendLine("</div>");
            return sb.ToString();
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

            var settings = FileController.LoadSaveData();

            var node = settings.Canvases.FirstOrDefault(n => n.NodeId == update.NodeId);
            var streamer = settings.Streamers.FirstOrDefault(s => s.PrivateId == update.PrivateId);

            if (node == null || streamer == null)
                return new JsonResult(new { success = false, message = "Node or streamer not found" });

            node.LinkedStreamerId = update.PrivateId;
            FileController.SaveFileData(settings);

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
            var _saveFile = FileController.LoadSaveData();
            var node = _saveFile.Canvases.FirstOrDefault(n => n.NodeId == request.NodeId);

            if (node == null)
                return new JsonResult(new { success = false, message = "Node not found" });

            // Update node color only if it's a main color update
            if (!request.IsAlt)
                node.ColorHex = request.ColorHex;

            if (!string.IsNullOrEmpty(node.LinkedStreamerId))
            {
                var streamer = _saveFile.Streamers.FirstOrDefault(s => s.PrivateId == node.LinkedStreamerId);
                if (streamer != null)
                {
                    if (request.IsAlt)
                        streamer.AltColorHex = request.ColorHex;
                    else
                        streamer.StreamerColor = request.ColorHex;
                }
            }

            FileController.SaveFileData(_saveFile);

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
            var streamer = Streamers.FirstOrDefault(s => s.PrivateId == node?.LinkedStreamerId);

            return !string.IsNullOrWhiteSpace(streamer?.DisplayName)
                ? streamer.DisplayName
                : !string.IsNullOrWhiteSpace(streamer?.Name)
                    ? streamer.Name
                    : $"Node {nodeId}";
        }


        public class EffectPayload
        {
            public int NodeId { get; set; }
            public string ColorHex { get; set; }
            public string DisplaySetting { get; set; }
        }

        //public async Task<IActionResult> OnPostUpdateEffectAsync()
        //{
        //    using var reader = new StreamReader(Request.Body);
        //    var body = await reader.ReadToEndAsync();

        //    var payload = JsonSerializer.Deserialize<EffectPayload>(body);

        //    if (payload == null)
        //        return BadRequest();

        //    // Load current save.json
        //    var _saveFile = FileController.LoadSave();
        //    var node = _saveFile.Canvases.FirstOrDefault(n => n.NodeId == payload.NodeId);


        //    var updated = nodes.Select(n =>
        //    {
        //        if (n.GetProperty("NodeId").GetInt32() == payload.NodeId)
        //        {
        //            var obj = new Dictionary<string, object>
        //            {
        //                ["NodeId"] = payload.NodeId,
        //                ["ColorHex"] = payload.ColorHex,
        //                ["LinkedStreamerId"] = n.GetProperty("LinkedStreamerId").GetString(),
        //                ["DisplaySetting"] = payload.DisplaySetting
        //            };
        //            return obj;
        //        }
        //        else
        //        {
        //            return JsonSerializer.Deserialize<Dictionary<string, object>>(n.GetRawText());
        //        }
        //    }).ToList();

        //    // Write back to file
        //    var newJson = JsonSerializer.Serialize(new { metadata = root.GetProperty("metadata"), nodes = updated });
        //    System.IO.File.WriteAllText(path, newJson);

        //    return new JsonResult(new { success = true });
        //}

        public class UpdateModeRequest
        {
            public string Mode { get; set; } = "on";
        }

        public IActionResult OnPostSetMode([FromBody] UpdateModeRequest request)
        {
            var settings = FileController.LoadAppSettings();
            settings.Mode = request.Mode.ToLower() == "off" ? "off" : "on";
            FileController.SaveAppSettings(settings);

            return new JsonResult(new { success = true });
        }




        /// <summary>
        /// Node ID normalization for display purposes. So that nodes start at top right and bottom right 
        /// Not sure this is needed as long as IDs are consistent in the JSON file.
        /// </summary>
        /// <param name="nodeId"></param>
        /// <returns></returns>
        public int Transform(int nodeId)
        {
            return nodeId < 8 ? 15 - nodeId : nodeId - 8;
        }
        private int? LogicalNodeFromPreviewIndex(int previewIndex)
        {
            // First row: previewIndex 0–7 maps directly to NodeId 0–7
            if (previewIndex < 8) return previewIndex;
            // Second row: previewIndex 8–15 maps to NodeId 8–15
            return previewIndex;
        }


    }

}
