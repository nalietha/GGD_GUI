using GGDTwitchAPI;
using Microsoft.AspNetCore.SignalR;
using TwitchLib.Api.Helix.Models.Entitlements;
using GGD_Display.Models;


namespace GGD_Display
{
    public class RefreshTwitchStatusService : BackgroundService
    {
        private readonly IHubContext<TwitchHub> _hub;
        private readonly TwitchApiService _twitch;
        private readonly ILogger<RefreshTwitchStatusService> _logger;

        // Flag to ensure we only send updates on the first run
        private bool _firstRun = true;


        public RefreshTwitchStatusService(
            IHubContext<TwitchHub> hub,
            TwitchApiService twitch,
            ILogger<RefreshTwitchStatusService> logger)
        {
            _hub = hub;
            _twitch = twitch;
            _logger = logger;
        }

        protected override async Task ExecuteAsync(CancellationToken stoppingToken)
        {
            while (!stoppingToken.IsCancellationRequested)
            {
                try
                {
                    var settings = FileController.LoadSave();

                    var linkedStreamerIds = settings.Canvases
                        .Select(n => n.LinkedStreamerId)
                        .Distinct()
                        .ToList();

                    var twitchIds = settings.Streamers
                        .Where(s => linkedStreamerIds.Contains(s.PrivateId) && !string.IsNullOrEmpty(s.PublicStreamerId))
                        .Select(s => s.PublicStreamerId)
                        .ToList();

                    var status = await _twitch.GetLiveStatus(twitchIds);
                    // Debugging for live status
                    _logger.LogInformation("Checked {Count} streamers for live status:", twitchIds.Count);

                    foreach (var id in twitchIds)
                    {
                        var isLive = status.TryGetValue(id, out var live) && live;
                        _logger.LogInformation($"Streamer ID: {id} => {(isLive ? "LIVE" : "OFFLINE")}");
                    }
                    // end of debugging statement

                    foreach (var streamer in settings.Streamers)
                    {
                        if (!string.IsNullOrEmpty(streamer.PublicStreamerId) &&
                            status.TryGetValue(streamer.PublicStreamerId, out bool isLive))
                        {
                            if (streamer.IsLive != isLive)
                            {
                                streamer.IsLive = isLive;
                            }

                            if (_firstRun || streamer.IsLive != isLive)
                            {
                                await _hub.Clients.All.SendAsync("updateStreamer", streamer);
                            }
                        }
                    }

                    _firstRun = false;

                    FileController.SaveFile(settings);
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "Error while refreshing Twitch statuses");
                }

                await Task.Delay(TimeSpan.FromSeconds(30), stoppingToken);
            }
        }
    }

}
