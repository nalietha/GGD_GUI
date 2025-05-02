using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

namespace GGD_Twitch_API
{
    internal class APICallStreamerListModel
    {
        String streamerName { get; set; }
        bool isLive { get; set; }
        bool isCollab { get; set; }
        //string list collabPartners{get; set;}
    }
}
