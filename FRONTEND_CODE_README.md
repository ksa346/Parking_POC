# Blazor Frontend Code Reference

## 1. Application overview

This frontend is a Blazor Server application for a parking-lot proof of concept. It combines two counting strategies:

- Spot-based occupancy estimation from aisle-camera sampling.
- Entry and exit delta counting from gate cameras.

The application also includes evidence review, manual operational overrides, and a dashboard that can display backend occupancy telemetry and chat responses.

At a high level:

1. `Program.cs` wires DI, HTTP access, websocket services, and static-file exposure.
2. Razor pages render operational views and mutate scoped state objects.
3. `ParkingService` mediates backend HTTP APIs.
4. Websocket flows update occupancy and live entry-exit counts.

## 2. Startup and hosting

### `Program.cs`

Purpose:

- Creates the Blazor Server host.
- Registers all state and service dependencies.
- Configures backend HTTP access.
- Exposes evidence folders as static content.

Behavior summary:

- Adds Razor Pages and Server-Side Blazor.
- Reads `BackendUrl` from configuration, defaulting to `http://localhost:8000`.
- Registers `ParkingService` as a typed `HttpClient` with a 30-minute timeout.
- Registers `OccupancyWebSocketService` as a singleton because it can maintain a long-lived background subscription.
- Registers `LiveCountsWebSocketService` as scoped, though the current `/live-counts` page uses its own direct websocket implementation instead of injecting this service.
- Registers per-session state containers:
  - `EntryExitState`
  - `ConfigurationState`
  - `GarageSelectionState`
  - `ParkingReservation`
- Maps the Blazor hub and `_Host` fallback page.

Operational significance:

- The static-file setup is critical for evidence review because `DetectionEvidence.razor` builds URLs that point into these exposed folders.

### `_Host.cshtml`

Purpose:

- Hosts the Blazor application in server-prerendered mode.

Behavior summary:

- Uses `_Layout.cshtml` as the outer Razor Pages layout.
- Renders the `App` component using `ServerPrerendered` mode.

### `_Layout.cshtml`

Purpose:

- Defines the HTML shell and shared client assets.

Behavior summary:

- Sets page metadata and base path.
- Loads `css/app.css`.
- Loads Chart.js from CDN for dashboard history charts.
- Loads `blazor.server.js` and `js/chartInterop.js`.
- Includes the standard Blazor error UI banner.

### `App.razor`

Purpose:

- Defines route resolution for the application.

Behavior summary:

- Uses `Router` against the application assembly.
- Uses `MainLayout` as the default layout for resolved routes.
- Moves keyboard focus to the first `h1` after navigation.
- Renders a minimal not-found message for unmatched routes.

### `_Imports.razor`

Purpose:

- Centralizes namespaces commonly used across components.

Effect:

- Makes core Blazor, JS interop, model, service, and state namespaces available without per-file imports.

## 3. Data contracts and models

All domain models currently live in `Models/ParkingModels.cs`.

### `OccupancyData`

Purpose:

- Represents the current occupancy snapshot returned by the backend or websocket.

Fields:

- `OccupiedSpots`: Current occupied count.
- `AvailableSpots`: Current available count.
- `TotalSpots`: Total tracked spaces.
- `OccupancyPercent`: Occupancy percentage across tracked spaces.
- `Zones`: Per-zone breakdown for comparative display.
- `Timestamp`: Backend timestamp string.
- `DetectionMethod`: Label describing the backend model or strategy.
- `Confidence`: Confidence summary string.

Used by:

- `Dashboard.razor`
- `OccupancyWebSocketService`
- The partially retained occupancy logic in `EntryExit.razor`

### `EntryExitResult`

Purpose:

- Represents a completed entry-exit batch-processing response.

Fields:

- Source paths: `EntryVideoPath`, `ExitVideoPath`.
- Processing parameters: `ModelPath`, `ConfidenceThreshold`, `FrameStride`.
- Count outputs: `EnteredCount`, `ExitedCount`.
- Per-stream details: `Entry`, `Exit`.
- Output folders and processed-video references:
  - `SavedOutputDir`
  - `ProcessedOutputDir`
  - `ProcessedEntryVideoPath`
  - `ProcessedExitVideoPath`
  - `ProcessedEntryVideoUrl`
  - `ProcessedExitVideoUrl`

### `EntryExitStreamResult`

Purpose:

- Represents the entry-side or exit-side processing result within an `EntryExitResult`.

Fields:

- Source and media metadata: `VideoPath`, `DurationSeconds`, `Fps`, `FrameCount`.
- Processing controls: `FrameStride`.
- Result count: `Count`.
- Tripwire used: `Tripwire`.
- Processed media outputs: `ProcessedVideoPath`, `ProcessedVideoUrl`.

### `EntryExitTripwireResult`

Purpose:

- Carries tripwire coordinates returned by the backend.

Fields:

- `X1`, `Y1`, `X2`, `Y2`: Tripwire coordinates.
- `DeadbandPx`: Deadband used near the tripwire.

### `LiveCountEvent`

Purpose:

- Represents a websocket event emitted by the live-counts backend.

Fields:

- `Event`: Event type such as `count` or `error`.
- `Seq`: Sequence number.
- `Timestamp`: Event time.
- `Count`: Count payload.
- `Message`: Optional error or status message.

### `ActivityLog`

Purpose:

- Stores recent operator actions shown in configuration screens.

Fields:

- `TimeStamp`: When the action happened.
- `Type`: Category of action.
- `Details`: Human-readable details.

### `LiveCountsRequest`

Purpose:

- Root request object for the POST `/live-counts` sampling API.

Fields:

- `Streams`: Collection of per-camera sampling definitions.

### `LiveCountStreamRequest`

Purpose:

- Describes one camera sampling job.

Fields:

- `StreamId`: Identifier expected back in the response.
- `Source`: RTSP or other source URL.
- `Conf`: Confidence threshold.
- `Iou`: IoU threshold.
- `Regions`: Left and right polygons used by the backend counting logic.

### `LiveCountRegions`

Purpose:

- Groups left and right polygon definitions for spot-based occupancy counting.

Fields:

- `RegionLeft`: Polygon points for the left region.
- `RegionRight`: Polygon points for the right region.

### `LiveCountStreamResponse`

Purpose:

- Represents one stream result returned by the live-counts API.

Fields:

- `StreamId`: Correlation identifier.
- `Count`: Occupancy count for the stream.
- `Error`: Stream-specific failure message.

### `VideoCountsRequest`

Purpose:

- Convenience request shape for video counting scenarios.

Fields:

- `VideoPaths`: Paths to process.
- `ConfidenceThreshold`: Threshold to use.
- `IntervalSeconds`: Sampling interval.

## 4. Service layer

## `ParkingService`

Purpose:

- Centralizes HTTP calls to the backend API.
- Normalizes a few response-shape differences.
- Supplies URLs used by page-level image refresh logic.

Constructor behavior:

- Receives typed `HttpClient`, configuration, and logger.
- Reads entry-exit defaults from configuration.
- Caches case-insensitive JSON options.

### `GetOccupancyAsync(CancellationToken)`

Purpose:

- GETs `/api/v1/occupancy`.

Returns:

- `OccupancyData` on success.
- `null` on failure.

Notes:

- Exceptions are swallowed, making the page responsible for null-safe rendering.

### `GetStatsAsync(CancellationToken)`

Purpose:

- GETs `/api/v1/stats`.

Returns:

- `StatsData` or `null`.

### `GetVideoCountsAsync(List<string> videoPaths, int intervalSeconds, double confidence, string modelPath, CancellationToken)`

Purpose:

- POSTs to `/api/v1/developer/video-counts` for developer-oriented counting analysis.

Behavior details:

- Sends video paths, model path, interval, confidence, and a flag disabling annotated-frame saving.
- Throws an exception when the backend returns a non-success status.
- Merges batch results into a continuous `Counts` timeline by offsetting each video’s seconds by cumulative duration.
- Promotes scalar `Count` values into a single-item `Counts` list for single-video shorthand responses.

### `GetCameraOccupancyAsync(LiveCountsRequest request, CancellationToken)`

Purpose:

- POSTs to `/live-counts` to obtain per-camera occupancy estimates from static region definitions.

Returns:

- Dictionary keyed by stream ID with integer counts.
- `null` if the response status or JSON shape is unsupported.

Behavior details:

- Supports two response formats:
  - a bare array of `LiveCountStreamResponse`
  - an object with a `results` array
- Logs both errors and raw response bodies for troubleshooting.

### `GetEntryExitCountsAsync(...)`

Purpose:

- POSTs an entry-exit batch job to `/api/v1/developer/entry-exit-counts`.

Parameters:

- Accepts source paths, thresholds, frame stride, and optional explicit tripwire coordinates.

Behavior details:

- Uses configured model path.
- Builds default entry and exit tripwires if custom coordinates are not provided.
- Enables annotated-frame and processed-video generation.
- Throws with the backend error message when the API call fails.

Current implementation note:

- The request currently hard-codes `inference_width = 0` and `use_tripwire_roi = false` even though configuration values are parsed in the constructor. That indicates partially implemented request wiring.

## `OccupancyWebSocketService`

Purpose:

- Maintains an optional background websocket subscription for occupancy snapshots.

Lifecycle behavior:

- Reads `EnableOccupancyWebSocket` from configuration.
- If enabled, starts connecting immediately from the constructor.
- If disabled, marks status as `disabled` and does not open a socket.

Public state:

- `Status`: `disconnected`, `connected`, or `disabled` depending on runtime state.
- `Latest`: Most recent `OccupancyData` payload.

Events:

- `OnOccupancyUpdate`: Raised when a valid occupancy snapshot arrives.
- `OnStatusChange`: Raised on state transitions.

### `ConnectLoopAsync()`

Purpose:

- Opens a websocket to `/api/ws/occupancy` and reconnects with exponential backoff after failures.

Behavior details:

- Rebuilds the socket for each reconnect attempt.
- Converts `http` to `ws` and `https` to `wss`.
- Backs off from 1 second to a maximum of 30 seconds.

### `ReceiveLoopAsync(CancellationToken)`

Purpose:

- Receives occupancy messages and deserializes them into `OccupancyData`.

Behavior details:

- Updates `Latest` and raises `OnOccupancyUpdate` when parsing succeeds.
- Logs parse failures without breaking the loop.

### `SetStatus(string)`

Purpose:

- Internal helper that mutates `Status` and emits the status event.

### `Dispose()`

Purpose:

- Stops reconnect attempts and disposes the websocket.

## `LiveCountsWebSocketService`

Purpose:

- Provides a reusable websocket client for live-count streams.

Current usage note:

- It is registered in DI but not currently used by `Pages/LiveCounts.razor`, which instead manages its own websocket connection inline.

Events:

- `OnCountReceived`
- `OnStatusChanged`
- `OnError`

Public state:

- `Status`: `idle`, `connecting`, or `connected`.

### `StartAsync(string liveUrl, string modelPath, int intervalSeconds, double confidence)`

Purpose:

- Opens a websocket to `/api/ws/live-counts` and sends the initial configuration payload.

Behavior details:

- No-ops if already connected or connecting.
- Sends `live_url`, `model_path`, `interval_seconds`, `confidence_threshold`, and `save_annotated_frames=false`.
- Starts `ReceiveLoopAsync` in the background.

### `StopAsync()`

Purpose:

- Cancels the receive loop and closes the socket gracefully.

### `ReceiveLoopAsync(CancellationToken)`

Purpose:

- Processes websocket events as `LiveCountEvent` messages.

Behavior details:

- Emits `OnError` and drops back to `idle` when `event == error`.
- Emits `OnCountReceived` when `event == count`.
- Ignores unsupported event types.

### `SetStatus(string)`

Purpose:

- Internal helper to synchronize `Status` and emit a change event.

### `DisposeAsync()`

Purpose:

- Stops background work and disposes socket resources.

## 5. Shared state containers

State classes live under `Services/State` and are registered as scoped services unless noted otherwise.

## `GarageSelectionState`

Purpose:

- Stores the selected garage and zone for layout-level filters.

Data exposed:

- `Garages`: Fixed list of selectable garage names.
- `Zones`: Fixed list of selectable zone names.
- `SelectedGarage`: Current garage selection.
- `SelectedZone`: Current zone selection.

Events:

- `OnChange`: Raised whenever the selected garage or zone changes.

## `EntryExitState`

Purpose:

- Holds session-scoped operational values for live entry-exit counting, evidence browsing, and occupancy calculations.

Important fields:

- Counter values:
  - `EntryCount`
  - `ExitCount`
  - `TotalEntered`
  - `TotalExited`
  - `NetChange`
- Source configuration:
  - `EntryVideoPath`
  - `ExitVideoPath`
  - `Confidence`
  - `FrameStride`
- Tripwire coordinates:
  - `EntryX1`, `EntryY1`, `EntryX2`, `EntryY2`
  - `ExitX1`, `ExitY1`, `ExitX2`, `ExitY2`
- Occupancy state:
  - `CurrentAvailable`
  - `EEOccupied`
  - `SpotOccupied`
  - `GeneralOccupied`
  - `AdaOccupied`
  - `EvOccupied`
- Processed-media references:
  - `ProcessedEntryVideoUrl`
  - `ProcessedEntryVideoFileName`
  - `ProcessedExitVideoUrl`
  - `ProcessedExitVideoFileName`

Operational role:

- This is the main session bridge between `LiveCounts.razor`, `DetectionEvidence.razor`, and other occupancy displays.

### `ValidationRecord`

Purpose:

- Immutable session record representing a saved detection frame and its review status.

Fields:

- `Id`: Local sequence number.
- `Time`: Timestamp for display and ordering.
- `Frame`: Frame label.
- `FileName`: File stored on disk.
- `Count`: Signed count impact.
- `DetectionType`: `Entry` or `Exit`.
- `ValidationStatus`: `Pending`, `Approved`, or `Rejected` in UI terms.

## `ConfigurationState`

Purpose:

- Holds operator-managed parking configuration values.

Fields:

- `ParkingSpots`, `ReservedSpots`
- `TotalLotCount`
- `GeneralSpots`, `AdaSpots`, `EvSpots`
- `ActivityLogs`

Operational role:

- Serves as the mutable shared source for configuration and the recent activity table.

## `ParkingReservation`

Purpose:

- Session container for reservation inputs.

Fields:

- `ReservedSpots`, `StartDate`, `EndDate`, `StartTime`, `EndTime`

Current usage note:

- The page-level reservation workflow in `Configuration.razor` currently keeps its own local reservation list rather than persisting through this service.

## `CarDetection`

Purpose:

- Legacy or alternate shared state model for parking spots, reservation flags, and activity logs.

Fields:

- `ParkingSpots`, `ReservedSpots`, `EnableReservation`, `TotalLotCount`, `ActivityLogs`

Current usage note:

- This class is present but not clearly wired into current routed pages.

## 6. Routed pages

## `Pages/EntryExit.razor` route: `/`

Purpose:

- Displays spot-based occupancy derived from three aisle cameras: general, ADA, and EV.

Primary responsibilities:

- Render KPI cards for total, general, ADA, and EV capacity/availability.
- Render live MJPEG feeds for each camera.
- Poll the backend live-counts API every minute using fixed polygon regions.
- Push returned counts into `EntryExitState`.

Important private constants and fields:

- Stream IDs: `cam_01`, `cam_02`, `cam_03`.
- Region polygons: `Cam01RegionLeft/Right`, `Cam02RegionLeft/Right`, `Cam03RegionLeft/Right`.
- `_kpiTimer`: 60-second polling timer.
- RTSP URLs resolved from `CameraFeeds:Ada`, `CameraFeeds:Ev`, and `CameraFeeds:General`.

Computed properties:

- `AdaMjpegUrl`, `EvMjpegUrl`, `GeneralMjpegUrl`: Backend MJPEG proxy URLs.
- `EntryExitTotalLotCount`: Sum of general, ADA, and EV capacity.
- `CurrentOccupied`, `CurrentAvailable`, `CurrentOccupancyPercent`: Derived display values.
- `OccupancyColorClass`: Styling helper retained for commented-out UI.

### `OnInitializedAsync()`

Purpose:

- Initializes the route by fetching counts immediately and starting a 1-minute timer.

Behavior details:

- Sets websocket status to `disconnected`.
- Calls `FetchCameraOccupancyAsync()` once on load.
- Schedules repeated fetches through `InvokeAsync` to remain on the Blazor sync context.

### `FetchCameraOccupancyAsync()`

Purpose:

- Builds the `LiveCountsRequest` and sends it to the backend through `ParkingService`.

Behavior details:

- Defines one stream request per camera with configured RTSP source and hard-coded polygons.
- Applies returned counts to `Params.GeneralOccupied`, `Params.AdaOccupied`, and `Params.EvOccupied`.
- Recomputes `Params.SpotOccupied` and `Params.CurrentAvailable`.
- Logs the raw applied totals for troubleshooting.

### `TryGetCount(Dictionary<string,int>, params string[] keys)`

Purpose:

- Helper that returns the first matching count for one of several possible response keys.

### `BuildMjpegUrl(string?)`

Purpose:

- Converts a configured RTSP source into a backend MJPEG proxy URL.

Behavior:

- Produces `{BackendUrl}/api/v1/stream/mjpeg?url=...&fps=10`.

### `HandleUpdate(OccupancyData)`

Purpose:

- Legacy handler for occupancy websocket data.

Behavior details:

- Maintains historical entry and exit totals by diffing occupancy deltas when `_usingSampledCounts` is false.
- Updates `_occupancy`, timestamps, and occupancy state.

Current usage note:

- The page no longer subscribes to `WsService`, so this handler is effectively dormant.

### `HandleStatus(string)`

Purpose:

- Legacy websocket-status handler retained for the same dormant occupancy path.

### `ResetCounters()`

Purpose:

- Clears legacy derived-entry and exit event history.

### `OnMjpegLoadError()`

Purpose:

- Marks the route status as `error` when an MJPEG image fails to load.

### `Dispose()`

Purpose:

- Disposes the timer and unregisters legacy websocket handlers.

Implementation note:

- `Dispose()` unsubscribes from websocket events that are not currently subscribed in `OnInitializedAsync`, which suggests leftover logic from an earlier implementation.

## `Pages/LiveCounts.razor` route: `/live-counts`

Purpose:

- Runs live entry and exit counting against dedicated gate feeds.

Primary responsibilities:

- Display live garage KPIs using `EntryExitState` and `ConfigurationState`.
- Show backend-proxied live MJPEG entry and exit feeds.
- Open a websocket to a backend live-entry-exit stream.
- Update live entered, exited, and occupancy totals from websocket events.
- Capture entry and exit evidence directory paths from websocket payloads for later review.

Important fields:

- `_running`: Whether the live stream is active.
- `_errorMessage`: Current error shown to the operator.
- `_statusMessage`: Non-error status banner.
- `_processedOutputDir`: Placeholder for processed output references.
- `_selectedDetection`: Selected validation item for image analysis.
- `_validations`: Session review items mirrored from `EntryExitState`.
- `_ws`, `_wsCts`: Raw websocket and cancellation token for the live stream.

Computed properties:

- `EntryMjpegUrl`, `ExitMjpegUrl`: MJPEG proxy URLs for gate feeds.
- `LiveOccupied`: Clamped occupancy based on `Params.EEOccupied`.
- `LiveAvailable`: Total capacity minus live occupied.

### `OnInitialized()`

Purpose:

- Applies configured RTSP values into `EntryExitState` and restores validation state.

Behavior details:

- Reads `CameraFeeds:Entry` and `CameraFeeds:Exit`.
- Recalculates `Params.CurrentAvailable` from current config and occupancy.
- Copies `Params.Validations` and `Params.ImageFiles` into local lists.

### `OnAfterRenderAsync(bool firstRender)`

Purpose:

- Starts live processing after the first render.

Behavior details:

- Automatically calls `StartLive()` on first render when not already running.

### `StartLive()`

Purpose:

- Opens a websocket to `/api/ws/live-entry-exit-counts` and begins processing streaming count events.

Behavior details:

- Validates `BackendUrl` and exits with a visible error if missing.
- Builds the websocket URL by switching `http` to `ws` and `https` to `wss`.
- Sends a JSON configuration payload containing:
  - `entry_url`
  - `exit_url`
  - `confidence_threshold`
  - `model_path`
  - `interval_seconds`
  - `tick_seconds`
  - `max_stale_frames`
  - `max_match_distance_px`
  - `save_annotated_frames`
- Starts a background receive task.

Websocket event handling:

- `count`:
  - Reads `entered_count` and `exited_count`.
  - Computes deltas relative to the previous totals in `EntryExitState`.
  - Updates `Params.TotalEntered`, `Params.TotalExited`, `Params.NetChange`, `Params.EEOccupied`, and `Params.CurrentAvailable`.
  - Captures `entry_evidence_dir` and `exit_evidence_dir` into state for evidence review.
- `warning`:
  - Displays a stream-specific waiting message.
- `started`:
  - Marks the stream as live.
- `error`:
  - Displays the backend message and stops the live session.

### `StopLive()`

Purpose:

- Stops live processing and closes the websocket.

### `DisposeAsync()`

Purpose:

- Cancels and disposes websocket resources when the component is torn down.

### `ClearHistory()`

Purpose:

- Resets session review state, totals, processed-video references, and messages.

Behavior details:

- Clears both local lists and the shared `EntryExitState` mirrors.

### `ToUiVideoUrl(string?)`

Purpose:

- Appends a cache-busting timestamp query string to a backend relative URL.

Current usage note:

- Present for processed-video support, although the current processed-video panels are commented out.

### `Approve(int id)` and `Reject(int id)`

Purpose:

- Update a validation item’s status and synchronize it back to shared state.

### `SelectFrame(ValidationRecord item)`

Purpose:

- Toggles the selected detection shown in the photo-analysis panel.

### `BuildMjpegUrl(string?)`

Purpose:

- Same pattern as other pages: builds the backend MJPEG proxy URL.

### `Percent(int available, int total)`

Purpose:

- Returns a rounded integer percentage for KPI bars.

### `OnMjpegLoadError()`

Purpose:

- Shows a diagnostic message describing common causes of MJPEG load failures.

## `Pages/Configuration.razor` route: `/configuration`

Purpose:

- Provides manual controls for parking capacity, occupancy overrides, and reservation logging.

Primary responsibilities:

- Update total lot count.
- Update general, ADA, and EV configured capacity.
- Update live-count occupancy manually.
- Capture reservation windows and record reservation activity.
- Render the recent activity log.

Key local state:

- Temporary inputs for lot count, occupancy, subtype capacities, and reservation parameters.
- `Reservations`: Local list used to compute active reserved spots.
- `ErrorMessage`: Validation feedback for the reservation form.

Computed values:

- `AvailableSpots`: Total capacity minus entry-exit occupied count.
- `EntryExitTotalLotCount`: Sum of general, ADA, and EV capacities.
- `ActiveReservedSpots`: Count of currently active reservations from the local list.

### `UpdateLotCount()`

Purpose:

- Updates `Config.TotalLotCount` and recomputes current availability.

Side effects:

- Adds an `ActivityLog` entry.

### `UpdateGeneralTotal()`

Purpose:

- Updates configured general-space capacity.

Behavior details:

- Clamps occupied general spaces to the new configured maximum.
- Recomputes total occupied and current available values.
- Adds an activity log entry.

### `UpdateAdaTotal()`

Purpose:

- Same pattern as general-space updates, but for ADA capacity.

### `UpdateEvTotal()`

Purpose:

- Same pattern as general-space updates, but for EV capacity.

### `ReserveParkingSpots()`

Purpose:

- Validates reservation inputs and records a reservation activity.

Validation behavior:

- Requires a positive spot count.
- Rejects counts above total lot count.
- Requires start date.
- Ensures start date is not after end date.
- Ensures start time is not after end time.
- Requires both start and end time.

Side effects:

- Increments `Config.ReservedSpots`.
- Adds a readable activity log message summarizing the reservation window.
- Resets form fields.

Current implementation note:

- The local `Reservations` list is not appended to, so `ActiveReservedSpots` is computed from an empty collection unless this page is extended.

### `UpdateOccupancy()`

Purpose:

- Manually overrides `Params.EEOccupied` within the configured total lot range.

Side effects:

- Recomputes current available count.
- Adds an activity log entry.

## 7. Shared layout and navigation components

## `Shared/MainLayout.razor`

Purpose:

- Provides the application shell, branding, live clock, garage and zone filters, and responsive sidebar behavior.

Primary responsibilities:

- Render the sidebar, top bar, and secondary filter bar.
- Maintain the open or collapsed sidebar state.
- Display the current date and time in a fixed UTC-4 offset.
- Reflect and mutate `GarageSelectionState`.

Key fields:

- `isSidebarOpen`: Responsive navigation state.
- `UtcMinus4`: Fixed display offset.
- `clockTimer`: 1-second timer for display updates.
- `currentDateTime`: Current displayed local time.

Computed properties:

- `DisplayDate`, `DisplayDay`, `DisplayTime`.

### `OnInitialized()`

Purpose:

- Starts the clock and subscribes to garage-selection changes.

### `ToggleSidebar()`

Purpose:

- Expands or collapses the sidebar.

### `HandleGarageChanged(ChangeEventArgs)`

Purpose:

- Pushes the selected garage into `GarageSelectionState`.

### `HandleZoneChanged(ChangeEventArgs)`

Purpose:

- Pushes the selected zone into `GarageSelectionState`.

### `HandleGarageSelectionChanged()`

Purpose:

- Forces a rerender after shared state changes.

### `HandleClockTick(object?, ElapsedEventArgs)`

Purpose:

- Updates the displayed time each second.

### `Dispose()`

Purpose:

- Stops the timer and unsubscribes from state events.

## `Shared/NavMenu.razor`

Purpose:

- Renders the left navigation menu.

Behavior summary:

- Displays the selected garage name in uppercase.
- Provides links to `/`, `/live-counts`, `/detection-evidence`, and `/configuration`.
- Retains a commented-out dashboard link.

### `OnInitialized()`

Purpose:

- Subscribes to garage-selection changes so the section label stays synchronized.

### `HandleGarageSelectionChanged()`

Purpose:

- Requests a rerender when garage selection changes.

### `Dispose()`

Purpose:

- Unsubscribes from `GarageSelectionState.OnChange`.

## Empty shared components

The following component files currently exist but do not contain implementation:

- `Shared/RecentActivity.razor`
- `Shared/ReserveParking.razor`
- `Shared/UpdateLotCount.razor`

Interpretation:

- They appear to be placeholders for future component extraction from `Configuration.razor` or earlier refactors.

## 8. Client-side JavaScript

## `wwwroot/js/chartInterop.js`

Purpose:

- Provides the JavaScript interop layer used by the dashboard chart and chat scrolling behavior.

### `window.chartInterop._charts`

Purpose:

- Stores active Chart.js instances keyed by canvas ID so they can be safely replaced.

### `window.chartInterop.renderLine(canvasId, labels, occupied, total)`

Purpose:

- Renders or replaces a Chart.js line chart for occupancy history.

Behavior details:

- Destroys any previous chart bound to the same canvas.
- Creates two datasets:
  - `Occupied` as a filled blue line.
  - `Total Capacity` as a dashed gray line.
- Configures responsive behavior, axis styling, and bottom legend placement.

### `window.blazorScrollBottom(element)`

Purpose:

- Scrolls a chat container to its bottom after messages are appended.

## 9. Configuration file

## `appsettings.json`

Purpose:

- Supplies runtime configuration for the frontend.

Important values:

- Logging defaults.
- `AllowedHosts` wildcard.
- `BackendUrl`.
- `CameraFeeds` for entry, exit, ADA, EV, and general cameras.
- `EnableOccupancyWebSocket` currently set to `false`.
- Entry-exit model defaults and optional processing parameters.
- `Urls` binding set to `http://localhost:5002`.

Security note:

- The current file contains concrete RTSP credentials in plain text, which is acceptable for a PoC but should be externalized for production.

## 10. Project file

## `BlazorParking.csproj`

Purpose:

- Declares the application as an ASP.NET Core web project targeting .NET 8.

Notable settings:

- `TargetFramework = net8.0`
- Nullable reference types enabled.
- Implicit using directives enabled.
- Root namespace set to `BlazorParking`.

## 12. Functional flow summary

### Spot detection flow

1. `EntryExit.razor` loads RTSP sources for general, ADA, and EV cameras.
2. It sends region-based requests to `/live-counts` through `ParkingService.GetCameraOccupancyAsync()`.
3. The returned counts update `EntryExitState`.
4. KPI cards and live MJPEG panels reflect the updated state.

### Entry and exit flow

1. `LiveCounts.razor` reads entry and exit RTSP sources from configuration.
2. It opens a websocket to `/api/ws/live-entry-exit-counts`.
3. Streaming `count` events update total entered, total exited, net change, and occupied count.
4. Evidence directory paths from the backend are persisted into `EntryExitState`.
5. `DetectionEvidence.razor` discovers image files from those directories and exposes them for review.

### Dashboard flow

1. `Dashboard.razor` subscribes to `OccupancyWebSocketService` for live occupancy snapshots when enabled.
2. It falls back to HTTP for initial occupancy and statistics.
3. It refreshes the image frame endpoint every 2 seconds.
4. It renders history through Chart.js and sends AI assistant prompts through `ParkingService.SendChatAsync()`.

### Manual operations flow

1. `Configuration.razor` mutates `ConfigurationState` and `EntryExitState` directly.
2. Updates are reflected immediately across pages within the same Blazor session.
3. Operator actions are appended to `ConfigurationState.ActivityLogs` for display.

## 13. Current design observations

- The codebase mixes direct page-level websocket logic and reusable websocket services. `LiveCountsWebSocketService` exists but is not yet adopted by `LiveCounts.razor`.
- `EntryExit.razor` still retains dormant occupancy-websocket fields and handlers from an older approach.
- Several shared components are placeholders and suggest future refactoring opportunities.
- Configuration and reservation data are session-scoped in the frontend today; persistent schema support exists in SQL but is not yet wired into the Blazor UI.