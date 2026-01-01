using Microsoft.AspNetCore.Mvc;

namespace GGD_Display.Controllers
{
    public class AuditController : Controller
    {
        public IActionResult Index()
        {
            return View();
        }

        public bool AuditSaveFile()
        {


            return false;
        }

        public bool AuditAppsettingsFile()
        {


            return false;

        }

        private bool AuditVersion(string file)
        {
            // Files to check: save.json, appsettings.json, led_listener.json, ggd_display.json
            return false;
        }

    }
}
