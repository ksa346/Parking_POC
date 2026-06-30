# Azure GPU Setup Guide (Parking_POC)

This guide documents the full process to run the project on a GPU-enabled Azure compute instance.

## 1. Create or Select a GPU-Capable Azure Instance

Choose a VM/compute instance SKU with NVIDIA GPU, for example:

- Standard_NC4as_T4_v3
- Standard_NC6s_v3
- Standard_ND series

Make sure the instance status is Running.

## 2. Connect and Verify GPU Availability

Open terminal on the Azure instance and run:

```bash
nvidia-smi
```

Expected: GPU model details and driver version are shown.

If this fails, GPU driver is not ready on that instance image.

## 3. Prepare Python Environment

From project root:

```bash
cd backend
conda create -n parking312 python=3.12 -y
conda activate parking312
pip install -r requirements.txt
```

## 4. Install CUDA-Enabled PyTorch

Install a CUDA build of PyTorch (example: CUDA 12.4 wheels):

```bash
pip install --upgrade torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
```

If your driver/CUDA compatibility is different, choose the matching command from PyTorch official install selector.

## 5. Validate GPU from Python

Run this check in the same environment:

```bash
python -c "import torch; print('cuda_available=', torch.cuda.is_available()); print('device_count=', torch.cuda.device_count()); print('device_name=', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none')"
```

Expected:

- cuda_available=True
- device_count >= 1

## 6. Project-Specific GPU Behavior

Current code already auto-selects GPU in entry/exit tracking when available:

- backend/app/yolotracker_entry_exit.py
- Device logic: uses cuda if torch.cuda.is_available() else cpu

For training scripts, set device explicitly when needed (example: cuda:0).

## 7. Configure Host IP for Azure Instance

Use your own Azure compute instance IP (do not reuse another person IP).

Backend start command pattern:

```bash
uvicorn app.main:app --host <YOUR_AZURE_INSTANCE_IP> --port 8000
```

Frontend settings file:

- blazor-frontend/appsettings.json

Set:

- BackendUrl: http://<YOUR_AZURE_INSTANCE_IP>:8000
- Urls: http://<YOUR_AZURE_INSTANCE_IP>:5002

Then start frontend:

```bash
cd ../blazor-frontend
dotnet watch run
```

## 8. Access Pattern Reminder

- localhost works only inside the Azure instance session.
- From your local machine, use Azure forwarded/proxied access for browser testing.

## 9. Optional Docker GPU Setup

If using docker-compose for GPU workloads:

1. Install NVIDIA Container Toolkit on the host.
2. Enable GPU reservation block in docker-compose.yml (currently commented).
3. Restart Docker service.
4. Launch compose services.

## 10. Verification Checklist

Run these checks in order:

1. nvidia-smi shows GPU.
2. torch.cuda.is_available() returns True.
3. Backend health endpoint responds.
4. Inference logs show cuda device usage.
5. Frontend can reach backend URL with your instance IP.

## 11. Common Issues and Fixes

1. torch.cuda.is_available() is False
- Install CUDA-enabled torch wheel (not CPU-only build).
- Ensure you activated the correct conda environment.

2. nvidia-smi not found
- GPU driver is missing or instance is not GPU SKU.

3. Backend works in terminal but not in browser
- You are likely using localhost from local machine.
- Use forwarded/proxied URL from Azure.

4. CUDA out-of-memory
- Lower image size, batch size, stream count, or model size.

## 12. Quick Start Commands (After Initial Setup)

Terminal 1 (backend):

```bash
cd backend
conda activate parking312
uvicorn app.main:app --host <YOUR_AZURE_INSTANCE_IP> --port 8000
```

Terminal 2 (frontend):

```bash
cd blazor-frontend
dotnet watch run
```
