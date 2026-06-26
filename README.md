**How to setup Blazor Frontend (.NET)**  

Prerequisite before running –

1. Install .NET SDK 9.0 from https://dotnet.microsoft.com/download
2. Make sure dotnet is added to system PATH. To verify, open command prompt and type:
dotnet --version  
**For running the Blazor frontend –**  

1. Open a separate command prompt and go to the blazor-frontend folder –
cd blazor-frontend
2. Run it –
dotnet run Note – Frontend will be available at: http://localhost:5002
**Config to update before running –**  

Open blazor-frontend/appsettings.json and set:  

BackendUrl – point to where backend is running e.g. http://127.0.0.1:8000
Urls – address Blazor listens on e.g. http://localhost:5002
CameraFeeds – update Entry, Exit RTSP URLs to match your cameras
EntryExitModelPath – path to yolo_trained.pt
Note – For checking UI - http://localhost:5002
