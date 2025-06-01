using GGD_Display.Models;
using GGDTwitchAPI;
using System.Text.Json;

namespace GGD_Display
{
    public class Utilities
    {
        /// <summary>
        /// Returns the current application version.
        /// </summary>
        /// <returns>String Version number</returns>
        public static string GetAppVersion()
        {
            return "1.4.5b"; // read from appsettings.config later
        }

        public static string RGBToHex(int r, int g, int b)
        {
            return $"#{r:X2}{g:X2}{b:X2}";
        }

        public int Transform(int x)
        {
            return x < 8 ? 15 - x : x - 8;
        }
        //private bool MigrateSettingsIfNeeded(AppSettings settings)
        //{

        //}

        //public string GetStreamerNameForNode(int nodeId)
        //{
        //    var node = Canvases.FirstOrDefault(n => n.NodeId == nodeId);
        //    var streamer = Streamers.FirstOrDefault(s => s.StreamerId == node?.LinkedStreamerId);
        //    return streamer?.Name ?? $"Node {nodeId}";
        //}
        public static class AdvancedSaveUtils
        {
            public static async Task<int> RefreshStreamerIdsFromNamesAsync(TwitchApiService twitch)
            {
                var settings = FileController.LoadSaveData();

                var streamersToUpdate = settings.Streamers
                    .Where(s => string.IsNullOrWhiteSpace(s.PublicStreamerId) && !string.IsNullOrWhiteSpace(s.Name))
                    .ToList();

                if (!streamersToUpdate.Any())
                    return 0;

                var names = streamersToUpdate.Select(s => s.Name).ToList();
                var resolved = await twitch.GetInitialTwitchData(names);

                int updatedCount = 0;
                foreach (var streamer in streamersToUpdate)
                {
                    var match = resolved.FirstOrDefault(r => r.Name.Equals(streamer.Name, StringComparison.OrdinalIgnoreCase));
                    if (match != null)
                    {
                        streamer.PublicStreamerId = match.ID;
                        updatedCount++;
                    }
                }

                FileController.SaveFileData(settings);
                return updatedCount;
            }
        }

        public static class PluginLoader
        {
            public static List<AdultPlatformPlugin> LoadAdultSitePlugins(string path = "plugins/adult_sites.json")
            {
                if (!File.Exists(path)) return new List<AdultPlatformPlugin>();
                var json = File.ReadAllText(path);
                return JsonSerializer.Deserialize<List<AdultPlatformPlugin>>(json) ?? new();
            }
        }

    }

}
