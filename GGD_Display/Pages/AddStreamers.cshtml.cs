using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using System.Text.RegularExpressions;
using GGD_Display.Models;
using GGDTwitchAPI;
using System.Diagnostics;


namespace GGD_Display.Pages
{
    public class AddStreamersModel : PageModel
    {
        // Constructor
        private readonly TwitchApiService _twitch;
        private readonly ILogger<AddStreamersModel> _logger;
        public AddStreamersModel(TwitchApiService twitch, ILogger<AddStreamersModel> logger)
        {
            _twitch = twitch;
            _logger = logger;
        }

        public class StreamerInput
        {
            public List<string> Streamers { get; set; } = new();
        }

        public void OnGet()
        {

        }
        
        //public async Task<IActionResult> OnPostAddAsync([FromBody] StreamerInput inputText)
        //{
        //    var entries = inputText.Split('\n', StringSplitOptions.RemoveEmptyEntries)
        //                .Select(e => e.Trim())
        //                .Where(e => !string.IsNullOrWhiteSpace(e))
        //                .ToList();

        //    var settings = FileController.LoadSettings();
        //    var added = 0;

        //    foreach (var line in entries)
        //    {
        //        var input = ExtractUsernameAndPlatform(line);

        //        // Skip duplicates (by PrivateId or PublicStreamerId)
        //        bool alreadyExists = settings.Streamers.Any(s =>
        //            s.Name.Equals(input.Username, StringComparison.OrdinalIgnoreCase) &&
        //            s.Platform == input.Platform);

        //        if (alreadyExists)
        //            continue;

        //        if (input.Platform == "Twitch")
        //        {
        //            var twitchUsers = await _twitch.GetStreamersWithLiveStatus(new[] { input.Username });

        //            if (twitchUsers.Count == 0)
        //                continue;

        //            var twitchUser = twitchUsers[0];

        //            settings.Streamers.Add(new StreamerInfo
        //            {
        //                PrivateId = Guid.NewGuid().ToString(),
        //                Name = twitchUser.Name,
        //                PublicStreamerId = twitchUser.ID,
        //                Platform = "Twitch",
        //                StreamerColor = "#ffffff",
        //                IsLive = twitchUser.IsLive
        //            });
        //        }
        //        else
        //        {
        //            // Add custom streamer from another platform
        //            settings.Streamers.Add(new StreamerInfo
        //            {
        //                PrivateId = Guid.NewGuid().ToString(),
        //                Name = input.Username,
        //                Platform = input.Platform,
        //                StreamerColor = "#999999",
        //                IsLive = false
        //            });
        //        }

        //        added++;
        //    }

        //    if (added > 0)
        //        FileController.SaveSettings(settings);

        //    return RedirectToPage("Index");

        //}

        private StreamerInputInfo ExtractUsernameAndPlatform(string input)
        {
            input = input.Trim();

            var patterns = new Dictionary<string, string>
            {
                { "Twitch", @"twitch\.tv\/(?<name>[a-zA-Z0-9_]+)" },
                { "Chaturbate", @"chaturbate\.com\/(?<name>[a-zA-Z0-9_]+)" },
                { "MyFreeCams", @"myfreecams\.com\/(?<name>[a-zA-Z0-9_]+)" },
                { "Stripchat", @"stripchat\.com\/(?<name>[a-zA-Z0-9_]+)" },
                { "CamSoda", @"camsoda\.com\/(?<name>[a-zA-Z0-9_]+)" }
            };

            foreach (var pair in patterns)
            {
                var match = Regex.Match(input, pair.Value, RegexOptions.IgnoreCase);
                if (match.Success)
                {
                    return new StreamerInputInfo
                    {
                        Username = match.Groups["name"].Value.ToLower(),
                        Platform = pair.Key
                    };
                }
            }

            // Default fallback
            return new StreamerInputInfo
            {
                Username = input.ToLower(),
                Platform = "Custom"
            };
        }

        public async Task<IActionResult> OnPostAddAsync([FromBody] StreamerInput input)
        {
            if (input?.Streamers == null || input.Streamers.Count == 0)
                return new JsonResult(new { success = false });

            var names = input.Streamers.Select(ParseStreamerName).Distinct().ToList();
            var found = await _twitch.GetStreamersWithLiveStatus(names);

            var settings = FileController.LoadSave();
            int added = 0;

            foreach (var streamer in found)
            {
                if (!settings.Streamers.Any(s => s.PublicStreamerId == streamer.ID))
                {
                    settings.Streamers.Add(new StreamerInfo
                    {
                        PrivateId = Guid.NewGuid().ToString(),
                        PublicStreamerId = streamer.ID,
                        Name = streamer.Name,
                        StreamerColor = "#9146FF", // default Twitch purple
                        IsLive = streamer.IsLive,
                        Platform = "Twitch"
                    });
                    added++;
                }
            }

            FileController.SaveFile(settings);
            return new JsonResult(new { success = true, added });
        }

        private string ParseStreamerName(string input)
        {
            // Extract Twitch name from URL or return name
            if (Uri.TryCreate(input, UriKind.Absolute, out var uri))
            {
                return uri.Segments.Last().Trim('/');
            }
            return input;
        }

    }
}
