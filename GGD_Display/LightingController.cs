namespace GGD_Display
{
    /// <summary>
    /// Controller for the lighting system.
    /// uses Raspberry Pi GPIO pins to control the lights.
    /// </summary>
    public class LightingController
    {
        private Dictionary<int, (int Red, int Green, int Blue)> nodeColors;
        // leds  from FastLED library
        //private CRGB[] leds = new CRGB[112]; // 16 nodes * 7 leds per node

        public LightingController()
        {
            // Initialize the GPIO pins for the lights
            // This is where you would set up the GPIO pins for the lights
            // For example, using the Raspberry Pi GPIO library
            // using i2c to control the lights




            // Initialize the node colors with default values (e.g., all off/black)
            nodeColors = new Dictionary<int, (int Red, int Green, int Blue)>();
            for (int i = 0; i < 16; i++)
            {
                nodeColors[i] = (0, 0, 0); // Default to black (off)
            }


        }

        void ToggleLEDNode(int ledNode)
        {
            // Each Jewel node has 7 leds toggle each on/off
            // test the node if its current status is on, turn off else if off turn on
            if (ledNode == 0) // 0 Means it is off
            {
                SetLEDOn(ledNode);
            }
            else
            {
                SetLEDOff(ledNode);
            }
        }

        void SetLEDColor(int ledNode, int red, int green, int blue)
        {
            // Each Jewel node has 7 leds set each color
            for (int setLeds = ledNode * 7; setLeds < (ledNode * 7) + 7; setLeds++)
            {

                leds[setLeds] = CRGB(red, green, blue);
            }
        }

        void SetLEDOff(int ledNode)
        {
            // Each Jewel node has 7 leds set each off
            for (int setLeds = ledNode * 7; setLeds < (ledNode * 7) + 7; setLeds++)
            {
                // setting to black turns off the leds
                // Setting individual leds to black turns off the leds
                // brightness for the leds are not adjustable per led
                leds[setLeds] = CRGB::Black;
            }
        }


        void SetLEDOn(int ledNode)
        {
            // Each Jewel node has 7 leds set each on
            for (int setLeds = ledNode * 7; setLeds < (ledNode * 7) + 7; setLeds++)
            {
                // setting to white turns on the leds
                leds[setLeds] = CRGB::White;
            }

        }


    }
}
