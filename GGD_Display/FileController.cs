using System.Diagnostics;
using System.Text.Json;

namespace GGD_Display
{
    public class FileController : DisplayModels
    {
        // moved Create Json file and Version controll to index.cs, TODO: add the rest of the file controller items here
        var twitch = new TwitchApiService(Config.TwitchClientId, Config.TwitchClientSecret);
        await twitch.InitializeAsync();

        var streamers = LoadFromSaveFile(); // user-defined color, name, id
        await twitch.UpdateStreamerData(streamers);
        SaveToSaveFile(streamers);




    }
}
