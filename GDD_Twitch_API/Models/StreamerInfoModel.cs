using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

namespace GGDTwitchAPI.Models
{
    public class StreamerInfoModel
    {

        public string Name { get; set; } = string.Empty;// updated by the user. or the api call
        public string ID { get; set; } = string.Empty;// Updated by the api call or by the user
        public bool IsActive { get; set; } = false; // set by the web interface
        public string Color { get; set; } = "#FFFFFF"; // Set by the user or white
        public bool IsLive { get; set; } = false;    // Will be updated by the api calls
    }

}
