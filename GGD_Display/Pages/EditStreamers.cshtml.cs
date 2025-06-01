using GGD_Display.Models;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using System.Text.Json.Serialization;

namespace GGD_Display.Pages
{
    public class EditStreamersModel : PageModel
    {
        [BindProperty]
        public List<StreamerInfo> Streamers { get; set; } = [];

        public Dictionary<string, List<StreamerInfo>> GroupedStreamersByPlatform =>
            Streamers
                .GroupBy(s => s.Platform ?? "Unknown")
                .OrderBy(g => g.Key) // Sort platforms alphabetically
                .ToDictionary(
                    g => g.Key,
                    g => g.OrderBy(s => s.DisplayName ?? s.Name).ToList() // Sort streamers within group
                );
        public List<StreamerInfo> SortedStreamers => Streamers
            .OrderBy(s => s.Name)
            .ToList();

        public void OnGet()
        {
            var settings = FileController.LoadSaveData();
            Streamers = settings.Streamers;
        }

        public IActionResult OnPostUpdate(string PrivateId, string DisplayName)
        {
            var settings = FileController.LoadSaveData();
            var streamer = settings.Streamers.FirstOrDefault(s => s.PrivateId == PrivateId);
            if (streamer != null)
            {
                streamer.DisplayName = DisplayName;
                FileController.SaveFileData(settings);
            }

            return RedirectToPage();
        }

        public async Task<IActionResult> OnPostRemoveStreamerAsync([FromBody] RemoveStreamerRequest request)
        {
            if (string.IsNullOrEmpty(request.PrivateId))
                return new JsonResult(new { success = false, message = "No ID provided" });

            var settings = FileController.LoadSaveData();
            var removed = settings.Streamers.RemoveAll(s => s.PrivateId == request.PrivateId) > 0;

            // Also clear any nodes linked to this streamer
            foreach (var node in settings.Canvases)
            {
                if (node.LinkedStreamerId == request.PrivateId)
                {
                    node.LinkedStreamerId = "";
                    node.ColorHex = "#000000";
                }
            }

            FileController.SaveFileData(settings);

            return new JsonResult(new { success = removed });
        }

        public class RemoveStreamerRequest
        {
            [JsonPropertyName("privateId")]
            public string PrivateId { get; set; }
        }
    }
}
