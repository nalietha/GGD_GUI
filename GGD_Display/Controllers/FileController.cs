using System.Diagnostics;
using System.Text.Json;
using System.Xml.Linq;
using GGD_Display.Models;
using static GGD_Display.Utilities;

public static class FileController
{
    private static readonly string _saveFile = "wwwroot/data/save.json";
    public static AppSettings LoadSettings()
    {
        if (!File.Exists(_saveFile))
        {
            return new AppSettings
            {
                Metadata = new Metadata
                {
                    Version = GetAppVersion(),
                    LastUpdated = DateTime.UtcNow
                },
                Canvases = new List<NodeInfo>(),
                Streamers = new List<StreamerInfo>()
            };
        }

        var json = File.ReadAllText(_saveFile);

        var options = new JsonSerializerOptions
        {
            PropertyNameCaseInsensitive = true
        };

        return JsonSerializer.Deserialize<AppSettings>(json, options) ?? new AppSettings();
    }

    public static void SaveSettings(AppSettings settings)
    {
        settings.Metadata.LastUpdated = DateTime.UtcNow;
        var options = new JsonSerializerOptions { WriteIndented = true };
        var json = JsonSerializer.Serialize(settings, options);
        File.WriteAllText(_saveFile, json);
    }

    public static void CreateJSONSaveFile()
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

}



