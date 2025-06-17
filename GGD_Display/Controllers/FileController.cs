using System.Diagnostics;
using System.Text.Json;
using System.Xml.Linq;
using GGD_Display.Models;
using static GGD_Display.Utilities;

public static class FileController
{
    private static readonly string _saveFile = "wwwroot/data/save.json";
    private static readonly string _AppsettingsSavePath = "appsettings.json";

    public static AppSaveData LoadSaveData()
    {
        if (!File.Exists(_saveFile))
        {
            return new AppSaveData
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

        return JsonSerializer.Deserialize<AppSaveData>(json, options) ?? new AppSaveData();
    }

    public static void SaveFileData(AppSaveData settings)
    {
        settings.Metadata.LastUpdated = DateTime.UtcNow;
        var options = new JsonSerializerOptions { WriteIndented = true };
        var json = JsonSerializer.Serialize(settings, options);
        File.WriteAllText(_saveFile, json);
    }

    /// <summary>
    /// Called when app detects no save file exists.
    /// Uses the save file models to create a default save file with initial settings.
    /// </summary>
    public static void CreateJSONSaveFile()
    {
        if (!System.IO.File.Exists(_saveFile))
        {
            var defaultSettings = new AppSaveData
            {
                Metadata = new Metadata
                {
                    Version = GetAppVersion(),
                    LastUpdated = DateTime.UtcNow
                },
                Canvases = Enumerable.Range(0, 16)
                    .Select(id =>
                    {
                        var node = new NodeInfo(); // uses default values from model
                        node.NodeId = id;          // override only what's unique
                        return node;
                    }).ToList(),
                Streamers = new List<StreamerInfo>() // Empty by default
            };

            var options = new JsonSerializerOptions { WriteIndented = true };
            var json = JsonSerializer.Serialize(defaultSettings, options);
            Directory.CreateDirectory(Path.GetDirectoryName(_saveFile)!);
            System.IO.File.WriteAllText(_saveFile, json);
        }
    }

    public static void CreateAppdataSaveFile()
    {
        // This method is not implemented in the original code, but you can add logic here if needed.
        // It might be used to create a save file in a different location, like the user's AppData folder.
        Debug.WriteLine("Create APPDATA Save File is not implemented yet.");

    }

    public static GGDDisplaySettings LoadAppSettings()
    {
        var path = Path.Combine(AppContext.BaseDirectory, "appsettings.json");
        var json = File.ReadAllText(path);
        var root = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(json);
        return root != null && root.TryGetValue("GGDDisplay", out var section)
            ? JsonSerializer.Deserialize<GGDDisplaySettings>(section.GetRawText())!
            : new GGDDisplaySettings();
    }

    public static void SaveAppSettings(GGDDisplaySettings updatedGGDDisplay)
    {
        var path = Path.Combine(AppContext.BaseDirectory, "appsettings.json");
        var json = File.ReadAllText(path);

        // Parse full JSON file
        using var doc = JsonDocument.Parse(json);
        var root = JsonSerializer.Deserialize<Dictionary<string, object>>(json);

        // Replace or insert GGDDisplay section
        root["GGDDisplay"] = updatedGGDDisplay;

        // Serialize and save full object
        var options = new JsonSerializerOptions
        {
            WriteIndented = true,
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase
        };

        var updatedJson = JsonSerializer.Serialize(root, options);
        File.WriteAllText(path, updatedJson);
    }


}



