using Microsoft.AspNetCore.SignalR;
using GGD_Display.Models;
using System.Threading.Tasks;


public class TwitchHub : Hub
{

    public async Task JoinDisplay(string connectionGroup)
    {
        await Groups.AddToGroupAsync(Context.ConnectionId, connectionGroup);
    }

    // Server -> clients
    public async Task BroadcastStreamerUpdate(StreamerInfo streamer)
    {
        await Clients.All.SendAsync("updateStreamer", streamer);
    }
}

