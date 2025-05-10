namespace GGD_Display
{
    public class DisplayModels
    {
        public class CanvasInfo
        {
            public int CanvasId { get; set; }
            public string CanvasTitle { get; set; } = string.Empty;
            public string ColorRGB { get; set; } = "#8956FB"; // Twitch purple
            public int LinkedStreamerId { get; set; }
        }

        public class StreamerInfo
        {
            public string Name { get; set; } = "DefaultStreamer";
            public int StreamerId { get; set; } = 0;
            public int R { get; set; } = 255;
            public int G { get; set; } = 255;
            public int B { get; set; } = 255;
        }
        public class LightingMode
        {
            public string Name { get; set; }
            public string Icon { get; set; } // Ideally an emoji or short text representation
        }

        public class AppSettings
        {
            public string Version { get; set; } = "0.0.0.1a"; // release:EPICS:Storys:Tasks{a-alpha, b-beta}"
                                                              // alpha - untested changes, beta - testing changes
            public List<CanvasInfo> Canvases { get; set; } = new();
            public StreamerInfo Streamer { get; set; } = new();
        }
    }
}
