namespace GGD_Display
{

    public class ClientSideDisplay
    {


        //  Call on program start. Load in API client ID, and Access token.
        // Client id and Access token should be stored where?
        /// <summary>
        /// User will have to add their client ID, USing the various flows from the Twitch API
        /// No secrets should be needed as no PPI data will ever be stored. 
        /// </summary>
        public void GetAPIToken()
        {
            
        }
        /// <summary>
        /// Used when the user is changing the colors of the lights,
        /// Need to implement a way to store the colors for each node
        /// Stream Name File would have a color section for each streamer.
        /// </summary>
        
        public bool UpdateLightColors()
        {

            throw new NotImplementedException();
        }



    }
}
