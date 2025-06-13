using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using System.Text.Json;
using static GGD_Display.Utilities;
using static System.Runtime.InteropServices.JavaScript.JSType;

namespace GGD_Display.Pages
{
    public class SettingsModel : PageModel
    {
        [BindProperty]
        public bool StreamerEnabled { get; set; }

        [BindProperty]
        public bool AdultContentCheckEnabled { get; set; }

                [BindProperty]
        public int Brightness { get; set; }

        private readonly IWebHostEnvironment _env;

        public SettingsModel(IWebHostEnvironment env)
        {
            _env = env;
        }

        public void OnGet()
        {
            var path = Path.Combine("appsettings.json");
            var json = System.IO.File.ReadAllText(path);
            var data = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(json);

            if (data.TryGetValue("GGD_Display", out var ggdDisplayElem))
            {
                var ggdDisplay = ggdDisplayElem.Deserialize<Dictionary<string, JsonElement>>();

                if (ggdDisplay.TryGetValue("Brightness", out var brightnessElem) && brightnessElem.TryGetInt32(out var brightnessVal))
                    Brightness = brightnessVal;

                if (ggdDisplay.TryGetValue("StreamerModeEnabled", out var sme) && sme.ValueKind == JsonValueKind.True || sme.ValueKind == JsonValueKind.False)
                    StreamerEnabled = sme.GetBoolean();

                if (ggdDisplay.TryGetValue("AdultContentCheckEnabled", out var ace) && ace.ValueKind == JsonValueKind.True || ace.ValueKind == JsonValueKind.False)
                    AdultContentCheckEnabled = ace.GetBoolean();
            }
        }

        public IActionResult OnPost()
        {
            var path = Path.Combine("appsettings.json");
            var json = System.IO.File.ReadAllText(path);
            var data = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(json);

            if (!data.ContainsKey("GGD_Display"))
                return BadRequest("GGD_Display section missing.");

            var ggdDisplay = data["GGD_Display"].Deserialize<Dictionary<string, object>>();

            ggdDisplay["StreamerModeEnabled"] = StreamerEnabled;
            ggdDisplay["AdultContentCheckEnabled"] = AdultContentCheckEnabled;
            ggdDisplay["Brightness"] = Brightness;

            // Optional: Modify Mode string if needed (e.g., "static", "pulse", etc.)
            // ggdDisplay["Mode"] = "static";

            // Put updated section back into the root
            data["GGD_Display"] = JsonSerializer.Deserialize<JsonElement>(
                JsonSerializer.Serialize(ggdDisplay)
            );

            var updated = JsonSerializer.Serialize(data, new JsonSerializerOptions { WriteIndented = true });
            System.IO.File.WriteAllText(path, updated);

            return RedirectToPage();
        }

        #region Adult Settings
        public class ToggleAdultSettingRequest
        {
            public bool Enabled { get; set; }
        }

        public async Task<IActionResult> OnPostToggleAdultSettingAsync([FromBody] ToggleAdultSettingRequest request)
        {
            var json = await System.IO.File.ReadAllTextAsync("appsettings.json");
            using var doc = JsonDocument.Parse(json);
            var root = doc.RootElement.Clone();

            var updated = new Dictionary<string, object>();

            // Copy existing root
            foreach (var prop in root.EnumerateObject())
            {
                updated[prop.Name] = JsonSerializer.Deserialize<object>(prop.Value.GetRawText())!;
            }

            // Update the setting
            if (updated.TryGetValue("GGD_Display", out var displayObj) && displayObj is JsonElement display)
            {
                var dict = JsonSerializer.Deserialize<Dictionary<string, object>>(display.GetRawText())!;
                var plugins = PluginLoader.LoadAdultSitePlugins();

                dict["AdultContentCheckEnabled"] = request.Enabled;

                if (request.Enabled)
                {
                    dict["AdultContent"] = new
                    {
                        SupportedPlatforms = plugins.Select(p => p.Platform),
                        ApiKeys = plugins.Where(p => p.RequiresApiKey).ToDictionary(p => p.Platform, p => "")
                    };
                }
                else
                {
                    dict.Remove("AdultContent");
                }

                updated["GGD_Display"] = dict;
            }

            var options = new JsonSerializerOptions { WriteIndented = true };
            var newJson = JsonSerializer.Serialize(updated, options);
            await System.IO.File.WriteAllTextAsync("appsettings.json", newJson);

            return new JsonResult(new { success = true });
        }




        #endregion
    }
}