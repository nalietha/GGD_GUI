using Microsoft.AspNetCore.Components.Web.Virtualization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using System.Text.Json;
using static GGD_Display.DisplayModels;


namespace GGD_Display.Pages
{
    public class IndexModel : PageModel
    {
        private readonly ILogger<IndexModel> _logger;
        [BindProperty]
        public int Index { get; set; }
        [BindProperty]
        public string ColorHex { get; set; }
        public Dictionary<int, string> CanvasColors { get; set; } = new();
        

        public IndexModel(ILogger<IndexModel> logger)
        {
            _logger = logger;
        }



        public void OnGet()
        {
            CreateJSONSaveFile();
            Dictionary<int, string> CanvasData = LoadCanvasData();
            CanvasColors = CanvasData.ToDictionary(c => c.CanvasId, c => c.ColorRGB);
        }


        public void OnPostChangeColor()
        {
            LoadCanvasColors();
            if (!string.IsNullOrEmpty(ColorHex))
            {
                CanvasColors[Index] = ColorHex;
            }
            else
            {
                CanvasColors[Index] = "#" + new Random().Next(0x1000000).ToString("X6");
            }
            SaveCanvasColors();
        }



        public List<LightingMode> LightingModes { get; set; } = new()
        {
            new LightingMode { Name = "Pulse", Icon = "💡" },
            new LightingMode { Name = "Wave", Icon = "🌊" },
            new LightingMode { Name = "Static", Icon = "🔒" },
            new LightingMode { Name = "Rainbow", Icon = "🌈" },
            new LightingMode { Name = "Flash", Icon = "⚡" },
        };


        public void CreateJSONSaveFile()
        {
            string filePath = "wwwroot/data/settings.json";

            if (!System.IO.File.Exists(filePath))
            {
                var defaultCanvases = Enumerable.Range(1, 16).Select(i => new CanvasInfo
                {
                    CanvasId = i,
                    ColorRGB = "#8956FB",
                    LinkedStreamerId = 0
                }).ToList();

                var defaultStreamer = new StreamerInfo
                {
                    Name = "DefaultStreamer",
                    StreamerId = 123456,
                    R = 255,
                    G = 255,
                    B = 255
                };

                var settings = new AppSettings
                {
                    Canvases = defaultCanvases,
                    Streamer = defaultStreamer
                };

                var json = JsonSerializer.Serialize(settings, new JsonSerializerOptions
                {
                    WriteIndented = true
                });

                System.IO.Directory.CreateDirectory(Path.GetDirectoryName(filePath)!);
                System.IO.File.WriteAllText(filePath, json);
            }
        }
        public string GetAppVersion()
        {
            return "0.0.0.1a"; // read from appsettings.config later
        }





    }

}
