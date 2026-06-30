***Instruction to run the code:***

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

**For running the backend-**  
1. In anaconda prompt, first create Conda environment –  
conda create -n parking312 python=3.12 -y  
2. Activate it –  
conda activate parking312  
3. Install requirements by going inside backend folder –  
pip install -r requirements.txt  
4. Go to backend folder and then run it –  
uvicorn app.main:app --host 127.0.0.1 --port 8000  
Note – For checking backend health: http://127.0.0.1:8000/api/v1/health  

**Config to update before running –**  

Open blazor-frontend/appsettings.json and set:  

BackendUrl – point to where backend is running e.g. http://127.0.0.1:8000  
Urls – address Blazor listens on e.g. http://localhost:5002  
CameraFeeds – update Entry, Exit RTSP URLs to match your cameras  
EntryExitModelPath – path to yolo_trained.pt  
Note – For checking UI - http://localhost:5002  

## Azure Compute Instance: Current Setup (Documented Change Summary)

### What changed

1. Backend host binding changed from loopback to compute-instance IP.
2. Blazor frontend backend target changed to compute-instance IP and port 8000.
3. Blazor frontend listen URL changed to compute-instance IP and port 5002.

### Access expectation

1. `localhost` is only valid inside the compute instance session.
2. Browser access from your local machine should use forwarded/proxied access.

### Current configured values

Important note:

- The IP shown below (`10.182.55.16`) is an example from one Azure compute instance.
- You must use your own Azure compute instance IP in all places (backend `--host`, `BackendUrl`, and `Urls`).
- Do not copy another instance IP directly, because each Azure compute instance has its own IP.

Backend start command in terminal:

```bash
uvicorn app.main:app --host 10.182.55.16 --port 8000
```

Frontend start command in terminal:

```bash
dotnet watch run
```

Frontend config file:

- `blazor-frontend/appsettings.json`

Backend service entrypoint:

- `backend/app/main.py`

Verified frontend config values in `blazor-frontend/appsettings.json`:

- `BackendUrl`: `http://10.182.55.16:8000`
- `Urls`: `http://10.182.55.16:5002`
