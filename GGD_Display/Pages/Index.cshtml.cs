using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace GGD_Display.Pages
{
    public class IndexModel : PageModel
    {
        private readonly ILogger<IndexModel> _logger;
        [BindProperty]
        public int Index { get; set; }
        [BindProperty]
        public string ColorHex { get; set; }
        public List<string> Colors { get; set; } = Enumerable.Repeat("#cccccc", 16).ToList();

        public IndexModel(ILogger<IndexModel> logger)
        {
            _logger = logger;
        }

        public Dictionary<int, string> CanvasColors { get; set; } = new();


        public void OnGet()
        {
            for (int i = 1; i <= 16; i++)
            {
                CanvasColors[i] = "#cccccc"; // Default gray
            }
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

        public class LightingMode
        {
            public string Name { get; set; }
            public string Icon { get; set; } // Ideally an emoji or short text representation
        }

        public List<LightingMode> LightingModes { get; set; } = new()
    {
        new LightingMode { Name = "Pulse", Icon = "💡" },
        new LightingMode { Name = "Wave", Icon = "🌊" },
        new LightingMode { Name = "Static", Icon = "🔒" },
        new LightingMode { Name = "Rainbow", Icon = "🌈" },
        new LightingMode { Name = "Flash", Icon = "⚡" },
    };



    }

}
