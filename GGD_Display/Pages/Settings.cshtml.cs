using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using System.Text.Json;

namespace GGD_Display.Pages
{
    public class SettingsModel : PageModel
    {
        [BindProperty]
        public bool StreamerEnabled { get; set; }

        [BindProperty]
        public bool AdultContentCheckEnabled { get; set; }

        private readonly IWebHostEnvironment _env;

        public SettingsModel(IWebHostEnvironment env)
        {
            _env = env;
        }

        public void OnGet()
        {
            var path = Path.Combine(_env.WebRootPath, "data", "appsettings.json");
            var json = System.IO.File.ReadAllText(path);
            var doc = JsonDocument.Parse(json);
            var mode = doc.RootElement.GetProperty("Mode");
            StreamerEnabled = mode.GetProperty("StreamerEnabled").GetBoolean();
            AdultContentCheckEnabled = mode.GetProperty("AdultContentCheckEnabled").GetBoolean();
        }

        public IActionResult OnPost()
        {
            var path = Path.Combine(_env.WebRootPath, "data", "appsettings.json");
            var json = System.IO.File.ReadAllText(path);
            var data = JsonSerializer.Deserialize<Dictionary<string, object>>(json);

            var mode = new Dictionary<string, object>
            {
                ["StreamerEnabled"] = StreamerEnabled,
                ["AdultContentCheckEnabled"] = AdultContentCheckEnabled
            };

            data["Mode"] = mode;

            var updated = JsonSerializer.Serialize(data, new JsonSerializerOptions { WriteIndented = true });
            System.IO.File.WriteAllText(path, updated);

            return RedirectToPage();
        }
    }
}