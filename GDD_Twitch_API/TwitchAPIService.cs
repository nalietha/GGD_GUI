using System;
using System.Configuration;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text.Json;
using System.Threading.Tasks;
using System.Collections.Generic;
using GGDTwitchAPI.Models;

namespace GGDTwitchAPI
{

    public class TwitchApiService
    {
        private readonly string _clientId;
        private readonly string _clientSecret;
        private string _accessToken;

        public TwitchApiService(string clientId, string clientSecret)
        {
            _clientId = clientId;
            _clientSecret = clientSecret;
        }

        public async Task InitializeAsync()
        {
            using var client = new HttpClient();
            var response = await client.PostAsync($"https://id.twitch.tv/oauth2/token" +
                $"?client_id={_clientId}&client_secret={_clientSecret}&grant_type=client_credentials", null);

            response.EnsureSuccessStatusCode();
            var json = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
            _accessToken = json.RootElement.GetProperty("access_token").GetString();
        }

        public async Task<List<StreamerInfoModel>> GetInitialTwitchData(IEnumerable<string> namesOrIds, bool useIds = false)
        {
            var param = useIds ? "id" : "login";
            var query = string.Join("&", namesOrIds.Select(name => $"{param}={Uri.EscapeDataString(name)}"));

            using var client = CreateAuthorizedClient();
            var response = await client.GetAsync($"https://api.twitch.tv/helix/users?{query}");
            response.EnsureSuccessStatusCode();

            var json = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
            return json.RootElement.GetProperty("data").EnumerateArray().Select(user => new StreamerInfoModel
            {
                Name = user.GetProperty("display_name").GetString(),
                ID = user.GetProperty("id").GetString()
            }).ToList();
        }
        private async Task<HttpResponseMessage> SendWithRetry(Func<HttpClient, Task<HttpResponseMessage>> action)
        {
            using var client = CreateAuthorizedClient();
            var response = await action(client);

            if (response.StatusCode == System.Net.HttpStatusCode.Unauthorized)
            {
                await InitializeAsync(); // refresh token
                using var retryClient = CreateAuthorizedClient();
                response = await action(retryClient);
            }

            response.EnsureSuccessStatusCode();
            return response;
        }

        public async Task<Dictionary<string, bool>> GetLiveStatus(List<string> streamerIds)
        {
            var query = string.Join("&", streamerIds.Select(id => $"user_id={id}"));

            var response = await SendWithRetry(client => client.GetAsync($"https://api.twitch.tv/helix/streams?{query}"));

            var json = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
            var liveIds = json.RootElement.GetProperty("data")
                .EnumerateArray()
                .Select(entry => entry.GetProperty("user_id").GetString())
                .ToHashSet();

            return streamerIds.ToDictionary(id => id, id => liveIds.Contains(id));
        }

        public async Task<List<StreamerInfoModel>> GetStreamersWithLiveStatus(IEnumerable<string> namesOrIds, bool useIds = false)
        {
            var userInfo = await GetInitialTwitchData(namesOrIds, useIds);
            if (userInfo == null || userInfo.Count == 0)
                return new List<StreamerInfoModel>();

            var ids = userInfo.Select(u => u.ID).ToList();
            var liveStatus = await GetLiveStatus(ids);

            foreach (var user in userInfo)
            {
                user.IsLive = liveStatus.TryGetValue(user.ID, out bool isLive) && isLive;
            }

            return userInfo;
        }


        private HttpClient CreateAuthorizedClient()
        {
            if (string.IsNullOrEmpty(_accessToken))
            {
                throw new InvalidOperationException("Twitch API access token not initialized. Call InitializeAsync() first.");
            }

            var client = new HttpClient();
            client.DefaultRequestHeaders.Add("Authorization", $"Bearer {_accessToken}");
            client.DefaultRequestHeaders.Add("Client-Id", _clientId);
            return client;
        }

    }

}

