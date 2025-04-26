using System;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text.Json;
using System.Threading.Tasks;
using System.Collections.Generic;

namespace GGD_Twitch_API
{
    public class ggdTwitch
    {
        // Create call function
        GetLiveStatus(List<APICallSteamerListModel> slist)
        {

            var streamerList = new List<APICallSteamerListModel>();

        }


        public class TwitchUserInfo
        {
            public string DisplayName { get; set; }
            public string Id { get; set; }
            public bool IsLive { get; set; }
        }

        public class TwitchApiService
        {
            private readonly string _clientId;
            private readonly string _accessToken;
            private readonly HttpClient _httpClient;

            public TwitchApiService()
            {
                _clientId = ConfigurationManager.AppSettings["TwitchClientId"];
                _accessToken = ConfigurationManager.AppSettings["TwitchAccessToken"];
                _httpClient = new HttpClient();
                _httpClient.DefaultRequestHeaders.Add("Client-Id", _clientId);
                _httpClient.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", _accessToken);
            }

            public TwitchApiService(string clientId, string accessToken)
            {
                _clientId = clientId;
                _accessToken = accessToken;
                _httpClient = new HttpClient();
                _httpClient.DefaultRequestHeaders.Add("Client-Id", _clientId);
                _httpClient.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", _accessToken);
            }

            public async Task<TwitchUserInfo> GetUserInfoAsync(string username)
            {
                var response = await _httpClient.GetAsync($"https://api.twitch.tv/helix/users?login={username}");

                if (!response.IsSuccessStatusCode)
                {
                    throw new Exception($"Error fetching user info: {response.StatusCode}");
                }

                var content = await response.Content.ReadAsStringAsync();
                using var jsonDoc = JsonDocument.Parse(content);

                var user = jsonDoc.RootElement.GetProperty("data")[0];

                string id = user.GetProperty("id").GetString();
                string displayName = user.GetProperty("display_name").GetString();

                // Now check if user is live
                var streamResponse = await _httpClient.GetAsync($"https://api.twitch.tv/helix/streams?user_id={id}");
                var streamContent = await streamResponse.Content.ReadAsStringAsync();
                using var streamJson = JsonDocument.Parse(streamContent);

                bool isLive = streamJson.RootElement.GetProperty("data").GetArrayLength() > 0;

                return new TwitchUserInfo
                {
                    DisplayName = displayName,
                    Id = id,
                    IsLive = isLive
                };
            }
        }


        // Process call

        // Return to controller cleaned data in model collection
        // Name Active = 0
    }
}
