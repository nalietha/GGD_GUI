using GGD_Display;
using GGDTwitchAPI;
using System.Text.Json.Serialization;

var builder = WebApplication.CreateBuilder(args);

// Add services to the container.
builder.WebHost.UseUrls("http://localhost:5000");

builder.Services.AddRazorPages();
// JSON global declariotions
builder.Services.AddControllers().AddJsonOptions(options =>
{
    options.JsonSerializerOptions.PropertyNameCaseInsensitive = true;
    options.JsonSerializerOptions.PropertyNamingPolicy = null; // Keep property names as-is
    options.JsonSerializerOptions.WriteIndented = true;
    options.JsonSerializerOptions.DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull;
});

builder.Services.AddRazorPages().AddJsonOptions(options =>
{
    options.JsonSerializerOptions.PropertyNameCaseInsensitive = true;
    options.JsonSerializerOptions.PropertyNamingPolicy = null;
    options.JsonSerializerOptions.WriteIndented = true;
    options.JsonSerializerOptions.DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull;
});

// Add SignalR service
builder.Services.AddSignalR();
builder.Services.AddHostedService<RefreshTwitchStatusService>();

var config = builder.Configuration;
var clientId = config["Twitch:ClientId"];
var clientSecret = config["Twitch:ClientSecret"];

builder.Services.AddSingleton<TwitchApiService>(provider =>
{
    var twitch = new TwitchApiService(clientId!, clientSecret!);
    twitch.InitializeAsync().GetAwaiter().GetResult(); // Init on startup
    return twitch;
});


var app = builder.Build();
app.MapHub<TwitchHub>("/twitchhub");




// Configure the HTTP request pipeline.
if (!app.Environment.IsDevelopment())
{
    app.UseExceptionHandler("/Error");
    // The default HSTS value is 30 days. You may want to change this for production scenarios, see https://aka.ms/aspnetcore-hsts.
    app.UseHsts();
}

app.UseHttpsRedirection();
app.UseStaticFiles();

app.UseRouting();

app.UseAuthorization();

app.MapRazorPages();

app.Run();
