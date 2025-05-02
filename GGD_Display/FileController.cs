namespace GGD_Display
{
    /// <summary>
    /// Controller for the file system.
    /// </summary>
    public class FileController
    {

        public FileController()
        {
            // Initialize the file system
            CheckFilePermissions();
            // This is where you would set up the file system
            // For example, using System.IO to create directories and files
            CreateLocalData();
        }

        private string GetFilePath(string fileName)
        {
            // Get the file path for the given file name
            // This is where you would get the file path for the given file name
            // For example, using System.IO to get the file path
            return Path.Combine(AppDomain.CurrentDomain.BaseDirectory, fileName);
        }


        public void CreateLocalData()
        {
            // Create the local data directory if it doesn't exist
            string localDataPath = GetFilePath("LocalData");
            if (!Directory.Exists(localDataPath))
            {
                Directory.CreateDirectory(localDataPath);
            }
            // Create the streamer list file if it doesn't exist
            string streamerListFilePath = GetFilePath("save.json");
            if (!File.Exists(streamerListFilePath))
            {
                File.Create(streamerListFilePath).Dispose();
            }

        }
        private void CheckFilePermissions()
        {
            // Check if the application has permission to access the file system
            // This is where you would check if the application has permission to access the file system
            // For example, using System.Security.AccessControl to check permissions
            // If the application doesn't have permission, throw an exception
            // throw new UnauthorizedAccessException("Application does not have permission to access the file system");
            if (!Directory.Exists(AppDomain.CurrentDomain.BaseDirectory))
            {
                throw new UnauthorizedAccessException("Application does not have permission to access the file system");
            }
        }
        /// <summary>
        /// Saves the display settings to a file.
        /// streamer data
        /// current light settings
        /// each node has an index that will be used to relate to the streamer that uses that node
        /// JSON file will store the node settings before the avalable streamer settings for readablitly.
        /// Nodes will not be deleted only modified
        /// Streamers can be modified and deleted
        /// update streamers with color changes only
        /// </summary>
        public bool SaveDisplaySettings()
        {
            // Save the display settings to a file
            if (!File.Exists(GetFilePath("save.json")))
            {
                throw new FileNotFoundException("Save file not found");
            }

            // This is where you would save the display settings to a file

            using (StreamWriter sw = new StreamWriter(GetFilePath("save.json")))
            {
                // Write the display settings to the file
                // This is where you would write the display settings to the file
                sw.WriteLine("Display settings saved");


            }

            return true;
        }


    }
}
