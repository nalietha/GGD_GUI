# GGD_Streamer LED System

A Raspberry Pi-powered LED lighting display synchronized with internet streamers with a focus on Twitch but support for other more, risqué, content. Each LED node can be individually configured with display modes and effects.

## Features

- Real-time Twitch stream monitoring
- Optional checks for adult content presence (via public APIs)
- Per-node display configuration (color, effect, mode)
- Streamer-independent display mode (no internet required)
- Integrated .NET Web UI for color/effect configuration
- SignalR-based push updates to clients
- WS2811 LED support using GPIO on Raspberry Pi

## Showcase

Experience the system in action with a look at the custom-built LED node hardware and display stand.

### 🔧 Custom LED Nodes

Each node is built using WS2811-compatible RGB LEDs using DYImall 7LED Jewels, allowing for granular control and unique display modes per streamer or independent effect.

![Custom LED Nodes](images/custom_led_nodes.jpg)

### 🖼️ Display Stand

The display is mounted in a handcrafted stand with 16 neatly spaced node slots, cable management, and access to Raspberry Pi ports for power and debugging.

![Display Stand Front](images/display_stand_front.jpg)
![Display Stand Back](images/display_stand_back.jpg)

### 🎇 Live Demo

Watch the lights pulse to life when streamer mode is enabled.

![Lights On Animation](images/lights_on.gif)



## Project Structure

- **GGD_Display**: ASP.NET Core web interface to configure node settings, colors, and linked streamers.
- **TwitchAPI.dll**: Class library for fetching Twitch streamer status.
- **led_controller.py**: Python script running on Raspberry Pi to control WS2811 LEDs via GPIO.
- **save.json**: Stores persistent configuration for streamers and node mappings.

## Requirements

### Hardware

- Raspberry Pi Gen4/Zero2 or above (any model with GPIO support)
- Due to ARM7L support for .Net
- Python Flask will support older models.
- WS2811-compatible LED strip
- Power supply for LEDs

### Software

- .NET Core 3.1+ (for Web App)
- Python 3.9+
- Required Python packages:
  - `rpi_ws281x`
  - `signalrcore`
  - `requests`

## Configuration

1. **Web Interface**:
   - Configure colors, effects, and streamer links via the UI.
   - Toggle `Streamer Mode` and `Adult Check Mode`.

2. **`appsettings.json` (ASP.NET)**:
   ```json
   {
     "EnableStreamerMode": true,
     "EnableAdultCheck": false
   }
