# Blazor Parking Frontend

This project is a .NET 8 Blazor Server frontend for the UK Parking proof of concept. It provides four primary operator workflows:

- Spot-based occupancy monitoring from fixed aisle cameras.
- Live entry and exit counting from dedicated gate cameras.
- Manual configuration of capacity, occupancy baselines, and reservations.

The frontend is stateful and operator-centric. Most values are held in scoped Blazor services so that pages can share live counters, configuration, evidence paths, and review decisions during a user session.

## Runtime architecture

- Blazor Server hosts the UI and maintains per-user circuit state.
- `ParkingService` is the primary HTTP client for backend APIs.
- `OccupancyWebSocketService` subscribes to lot occupancy updates when enabled.
- `Pages/LiveCounts.razor` opens its own websocket for live entry and exit events.

## Main routes

- `/` renders spot-detection occupancy views from aisle-camera counts.
- `/live-counts` renders live entry and exit counting.
- `/configuration` renders manual controls for capacity, occupancy, and reservation values.

## Configuration

Primary runtime settings live in `appsettings.json`:

- `BackendUrl`: Base URL for backend HTTP and websocket endpoints.
- `CameraFeeds:Entry` and `CameraFeeds:Exit`: RTSP sources for gate counting.
- `CameraFeeds:Ada`, `CameraFeeds:Ev`, `CameraFeeds:General`: RTSP sources for spot-based occupancy sampling.
- `EnableOccupancyWebSocket`: Enables the background occupancy websocket service.
- `EntryExitModelPath`: Default backend model identifier for entry and exit counting.
- `EntryExitInferenceWidth`, `EntryExitUseTripwireRoi`, `EntryExitTripwireRoiPaddingPx`: reserved backend request settings, partially wired in the frontend service.
- `Urls`: Local Kestrel binding for the Blazor app.

## Detailed code reference

The full implementation guide is in [docs/CodeReference.md](docs/CodeReference.md). That document covers:

- Every C# class and record.
- Every documented method and event handler.
- Every routed Razor page and shared layout component.
- Data flow between UI, state containers, HTTP endpoints, and websocket endpoints.
- JavaScript interop responsibilities.
- SQL schema scripts and how each database object supports the PoC.

## Running locally

1. Install .NET 8 SDK.
2. Start the backend API expected by `BackendUrl`.
3. From this folder, run `dotnet build` to restore and compile.
4. Run `dotnet run` and open the configured local URL.