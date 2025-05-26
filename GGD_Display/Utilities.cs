using static GGD_Display.DisplayModels;
using System.Text.Json;

namespace GGD_Display
{
    public class Utilities
    {


        public static string RGBToHex(int r, int g, int b)
        {
            return $"#{r:X2}{g:X2}{b:X2}";
        }

        private bool MigrateSettingsIfNeeded(AppSettings settings)
        {
            bool updated = false;

            if (settings.Metadata == null)
            {
                settings.Metadata = new Metadata
                {
                    Version = "0.0.1",
                    LastUpdated = DateTime.UtcNow
                };
                updated = true;
            }

            if (settings.Metadata.Version == "0.0.1")
            {
                foreach (var canvas in settings.Canvases)
                {
                    if (canvas.ColorRGB.StartsWith("{")) // suspect it's an RGB JSON object
                    {
                        try
                        {
                            var colorObj = JsonSerializer.Deserialize<RGBColor>(canvas.ColorRGB);
                            if (colorObj != null)
                            {
                                canvas.ColorRGB = $"#{colorObj.R:X2}{colorObj.G:X2}{colorObj.B:X2}";
                                updated = true;
                            }
                        }
                        catch
                        {
                            // fallback: default to gray if invalid format
                            canvas.ColorRGB = "#CCCCCC";
                            updated = true;
                        }
                    }
                }

                settings.Metadata.Version = _appVersion;
                settings.Metadata.LastUpdated = DateTime.UtcNow;
            }

            return updated;
        }

        public string GetStreamerNameForNode(int nodeId)
        {
            var node = Canvases.FirstOrDefault(n => n.NodeId == nodeId);
            var streamer = Streamers.FirstOrDefault(s => s.StreamerId == node?.LinkedStreamerId);
            return streamer?.Name ?? $"Node {nodeId}";
        }


    }

}
