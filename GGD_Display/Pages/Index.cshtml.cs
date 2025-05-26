using Microsoft.AspNetCore.Components.Web.Virtualization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using System.Text.Json;
using static GGD_Display.DisplayModels;


namespace GGD_Display.Pages
{
    public class IndexModel : PageModel
    {

        private readonly string _saveFile = "wwwroot/data/save.json";

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
            CreateJSONSaveFile(); // Ensure file exists
            LoadSettings();
        }


        private void LoadSettings()
        {
            if (System.IO.File.Exists(_saveFile))
            {
                try
                {
                    var json = System.IO.File.ReadAllText(_saveFile);
                    var settings = JsonSerializer.Deserialize<AppSettings>(json, new JsonSerializerOptions
                    {
                        PropertyNameCaseInsensitive = true
                    });

                    if (settings != null)
                    {
                        // Load metadata (optional)
                        // var version = settings.Metadata?.Version;

                        // Load nodes into Canvases for UI
                        Canvases = settings.Canvases ?? new();

                        // Load streamers
                        Streamers = settings.Streamers ?? new();
                    }
                }
                catch (Exception ex)
                {
                    // Log or handle file/JSON errors
                    Console.WriteLine($"Failed to load settings: {ex.Message}");
                }
            }
            else
            {
                // File not found — optionally create a default
                Console.WriteLine("Save file not found. Consider calling CreateJSONSaveFile().");
            }
        }


        public string GetStreamerNameForNode(int nodeId)
        {
            var node = Canvases.FirstOrDefault(n => n.NodeId == nodeId);
            var streamer = Streamers.FirstOrDefault(s => s.StreamerId == node?.LinkedStreamerId);
            return streamer?.Name ?? $"Node {nodeId}";
        }

        public void SaveCanvasColor(string canvasID, string colorCode)
        {
            // Open the save file and update the color for the indicated canvas ID

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

        private void CreateJSONSaveFile()
        {
            if (!System.IO.File.Exists(_saveFile))
            {
                var defaultSettings = new AppSettings
                {
                    Metadata = new Metadata
                    {
                        Version = GetAppVersion(),
                        LastUpdated = DateTime.UtcNow
                    },
                    Canvases = Enumerable.Range(0, 16).Select(id => new NodeInfo
                    {
                        NodeId = id,
                        ColorHex = "#000000",
                        LinkedStreamerId = "",
                        DisplaySetting = "default"
                    }).ToList(),
                    Streamers = new List<StreamerInfo>() // Empty by default
                };

                var options = new JsonSerializerOptions { WriteIndented = true };
                var json = JsonSerializer.Serialize(defaultSettings, options);
                Directory.CreateDirectory(Path.GetDirectoryName(_saveFile)!);
                System.IO.File.WriteAllText(_saveFile, json);
            }
        }


        public string GetAppVersion()
        {
            return "0.0.0.3a"; // read from appsettings.config later
        }

        //private bool MigrateSettingsIfNeeded(AppSettings settings)
        //{
        //    bool updated = false;

        //    if (settings.Metadata == null)
        //    {
        //        // Assume legacy or missing version info
        //        settings.Metadata = new Metadata
        //        {
        //            Version = GetAppVersion(), // assume an old version
        //            LastUpdated = DateTime.UtcNow
        //        };
        //        updated = true;
        //    }

        //    // Example migration from 0.0.1 to 1.0.0
        //    //if (settings.Metadata.Version == "0.0.1")
        //    //{
        //    //    // Here you can apply any transformation needed
        //    //    // For example, update all canvas colors from hex to RGB object if changed

        //    //    // Update version
        //    //    settings.Metadata.Version = _appVersion;
        //    //    settings.Metadata.LastUpdated = DateTime.UtcNow;
        //    //    updated = true;
        //    //}

        //    return updated;
        //}

        private void SaveSettings(AppSettings settings)
        {
            var json = JsonSerializer.Serialize(settings, new JsonSerializerOptions { WriteIndented = true });
            System.IO.File.WriteAllText(_saveFile, json);
        }


    }

}
