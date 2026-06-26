using System.Net.Http.Json;
using System.Text.Json;
using BlazorParking.Models;

namespace BlazorParking.Services;

public class ParkingService(HttpClient http, IConfiguration config, ILogger<ParkingService> logger)
{
    private static readonly JsonSerializerOptions _json = new() { PropertyNameCaseInsensitive = true };
    private readonly string _entryExitModelPath = string.IsNullOrWhiteSpace(config["EntryExitModelPath"])
        ? "base"
        : config["EntryExitModelPath"]!.Trim();
    private readonly int _entryExitInferenceWidth = int.TryParse(config["EntryExitInferenceWidth"], out var w)
        ? Math.Max(0, w)
        : 1280;
    private readonly bool _entryExitUseTripwireRoi = bool.TryParse(config["EntryExitUseTripwireRoi"], out var useRoi)
        ? useRoi
        : false;
    private readonly int _entryExitTripwireRoiPaddingPx = int.TryParse(config["EntryExitTripwireRoiPaddingPx"], out var p)
        ? Math.Max(0, p)
        : 220;

    public async Task<OccupancyData?> GetOccupancyAsync(CancellationToken ct = default)
    {
        try { return await http.GetFromJsonAsync<OccupancyData>("/api/v1/occupancy", _json, ct); }
        catch { return null; }
    }

    public async Task<StatsData?> GetStatsAsync(CancellationToken ct = default)
    {
        try { return await http.GetFromJsonAsync<StatsData>("/api/v1/stats", _json, ct); }
        catch { return null; }
    }

    public async Task<List<HistoryEntry>> GetHistoryAsync(int hours = 24, CancellationToken ct = default)
    {
        try
        {
            return await http.GetFromJsonAsync<List<HistoryEntry>>(
                $"/api/v1/history?hours={hours}", _json, ct) ?? [];
        }
        catch { return []; }
    }

    public async Task<string?> SendChatAsync(string message, List<ChatMessage> history, CancellationToken ct = default)
    {
        try
        {
            var resp = await http.PostAsJsonAsync("/api/v1/chat",
                new ChatRequest { Message = message, History = history }, ct);
            var chat = await resp.Content.ReadFromJsonAsync<ChatResponse>(_json, ct);
            return chat?.Reply;
        }
        catch { return null; }
    }

    public async Task<(bool ok, UploadApplyResponse data)> UploadAndApplyVideoAsync(
        Stream fileStream, string fileName, string contentType, CancellationToken ct = default)
    {
        try
        {
            using var content = new MultipartFormDataContent();
            var fileContent = new StreamContent(fileStream);
            fileContent.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue(contentType);
            content.Add(fileContent, "file", fileName);
            Console.WriteLine(JsonSerializer.Serialize(content));

            var resp = await http.PostAsync("/api/v1/video/upload-and-apply", content, ct);
            var body = await resp.Content.ReadFromJsonAsync<UploadApplyResponse>(_json, ct)
                       ?? new UploadApplyResponse();
            if (string.IsNullOrWhiteSpace(body.Message) && !string.IsNullOrWhiteSpace(body.Error))
            {
                body.Message = body.Error;
            }
            return (resp.IsSuccessStatusCode, body);
        }
        catch (Exception ex)
        {
            return (false, new UploadApplyResponse { Message = ex.Message, Error = ex.Message });
        }
    }

    public async Task<(bool ok, string message)> SetStreamUrlAsync(string url, CancellationToken ct = default)
    {
        try
        {
            var resp = await http.PostAsJsonAsync("/api/v1/video/set-stream-url", new { url }, ct);
            var body = await resp.Content.ReadFromJsonAsync<Dictionary<string, string>>(_json, ct)
                       ?? new Dictionary<string, string>();
            var msg = body.GetValueOrDefault("message") ?? body.GetValueOrDefault("error") ?? "";
            return (resp.IsSuccessStatusCode, msg);
        }
        catch (Exception ex) { return (false, ex.Message); }
    }

    public string GetVideoFrameUrl() =>
        $"{http.BaseAddress?.ToString().TrimEnd('/')}/api/v1/video/frame";

    public async Task<VideoCountsResponse?> GetVideoCountsAsync(
        List<string> videoPaths,
        int intervalSeconds = 10,
        double confidence = 0.5,
        string modelPath = "base",
        CancellationToken ct = default)
    {
        try
        {
            var payload = new Dictionary<string, object?>
            {
                ["video_path"] = videoPaths,
                ["model_path"] = modelPath,
                ["interval_seconds"] = Math.Max(1, intervalSeconds),
                ["confidence_threshold"] = Math.Max(0.0, Math.Min(1.0, confidence)),
                ["save_annotated_frames"] = false,
            };

            var resp = await http.PostAsJsonAsync("/api/v1/developer/video-counts", payload, ct);
            if (!resp.IsSuccessStatusCode)
            {
                var errBody = await resp.Content.ReadFromJsonAsync<Dictionary<string, string>>(_json, ct)
                              ?? new Dictionary<string, string>();
                var errMsg = errBody.GetValueOrDefault("error") ?? errBody.GetValueOrDefault("detail") ?? $"HTTP {(int)resp.StatusCode}";
                throw new Exception(errMsg);
            }

            var result = await resp.Content.ReadFromJsonAsync<VideoCountsResponse>(_json, ct);
            if (result is null)
            {
                return null;
            }

            // Batch response: merge per-video counts, offsetting seconds by cumulative duration.
            if (result.Batch && result.Results is { Count: > 0 })
            {
                var merged = new List<VideoCountEntry>();
                int secondOffset = 0;
                foreach (var r in result.Results)
                {
                    if (r.Counts is { Count: > 0 })
                    {
                        merged.AddRange(r.Counts.Select(c =>
                            new VideoCountEntry { Second = c.Second + secondOffset, Count = c.Count }));
                    }
                    else if (r.Count.HasValue)
                    {
                        merged.Add(new VideoCountEntry { Second = secondOffset, Count = r.Count.Value });
                    }
                    secondOffset += Math.Max(1, (int)Math.Round(r.DurationSeconds));
                }
                result.Counts = merged;
            }

            // Single-video shorthand: promote scalar count into the list.

            if ((result.Counts == null || result.Counts.Count == 0) && result.Count.HasValue)
            {
                result.Counts = [new VideoCountEntry { Second = 0, Count = result.Count.Value }];
            }

            return result;
        }
        catch (Exception)
        {
            throw;
        }
    }

    /// <summary>
    /// POST /live-counts with a streams payload.
    /// Response body may be either:
    /// - an array of stream results, or
    /// - an envelope object containing a results array.
    /// </summary>
    public async Task<Dictionary<string, int>?> GetCameraOccupancyAsync(
        LiveCountsRequest request,
        CancellationToken ct = default)
    {
        try
        {
            var resp = await http.PostAsJsonAsync("/live-counts", request, ct);
            if (!resp.IsSuccessStatusCode)
            {
                var errorBody = await resp.Content.ReadAsStringAsync(ct);
                logger.LogWarning(
                    "Live-counts API failed. Status={StatusCode}, Body={Body}",
                    (int)resp.StatusCode,
                    errorBody);
                return null;
            }

            var rawBody = await resp.Content.ReadAsStringAsync(ct);
            logger.LogInformation("Live-counts API raw response: {RawBody}", rawBody);

            List<LiveCountStreamResponse>? items;
            using (var doc = JsonDocument.Parse(rawBody))
            {
                if (doc.RootElement.ValueKind == JsonValueKind.Array)
                {
                    items = JsonSerializer.Deserialize<List<LiveCountStreamResponse>>(rawBody, _json);
                }
                else if (doc.RootElement.ValueKind == JsonValueKind.Object
                         && doc.RootElement.TryGetProperty("results", out var resultsEl)
                         && resultsEl.ValueKind == JsonValueKind.Array)
                {
                    items = JsonSerializer.Deserialize<List<LiveCountStreamResponse>>(resultsEl.GetRawText(), _json);
                }
                else
                {
                    logger.LogWarning("Live-counts API response shape not supported. Body={Body}", rawBody);
                    return null;
                }
            }

            if (items is null || items.Count == 0)
            {
                logger.LogWarning("Live-counts API parsed but returned no stream results.");
                return null;
            }

            return items
                .Where(x => !string.IsNullOrWhiteSpace(x.StreamId))
                .ToDictionary(x => x.StreamId, x => x.Count);
        }
        catch
        {
            return null;
        }
    }

    public async Task<EntryExitResult?> GetEntryExitCountsAsync(
        string entryVideoPath,
        string exitVideoPath,
        double confidence = 0.9,
        int frameStride = 1,
        int? entryX1 = null, int? entryY1 = null, int? entryX2 = null, int? entryY2 = null,
        int? exitX1 = null, int? exitY1 = null, int? exitX2 = null, int? exitY2 = null,
        CancellationToken ct = default)
    {
        try
        {
            // Entry camera: vertical tripwire at x=1210, top quarter (cars cross gate moving left)
            object? entryLine = entryX1.HasValue
                ? new { x1 = entryX1, y1 = entryY1, x2 = entryX2, y2 = entryY2 }
                : new { x1 = 1210, y1 = 0, x2 = 1210, y2 = 270 };
            // Exit camera: horizontal tripwire at y=350, full width (cars move away from camera, centroid goes from ~380 upward)
            object? exitLine = exitX1.HasValue
                ? new { x1 = exitX1, y1 = exitY1, x2 = exitX2, y2 = exitY2 }
                : new { x1 = 0, y1 = 350, x2 = 1919, y2 = 350 };

            var payload = new Dictionary<string, object?>
            {
                ["entry_video_path"] = entryVideoPath,
                ["exit_video_path"] = exitVideoPath,
                ["model_path"] = _entryExitModelPath,
                ["confidence_threshold"] = confidence,
                ["frame_stride"] = Math.Max(1, frameStride),
                ["inference_width"] = 0,
                ["use_tripwire_roi"] = false,
                ["save_annotated_frames"] = true,
                ["generate_processed_videos"] = true,
                ["entry_line"] = entryLine,
                ["exit_line"] = exitLine
            };

            var resp = await http.PostAsJsonAsync("/api/v1/developer/entry-exit-counts", payload, ct);
            if (resp.IsSuccessStatusCode)
                return await resp.Content.ReadFromJsonAsync<EntryExitResult>(_json, ct);

            // Surface the backend error message
            var errBody = await resp.Content.ReadFromJsonAsync<Dictionary<string, string>>(_json, ct)
                          ?? new Dictionary<string, string>();
            var errMsg = errBody.GetValueOrDefault("error") ?? errBody.GetValueOrDefault("detail") ?? $"HTTP {(int)resp.StatusCode}";
            throw new Exception(errMsg);
        }
        catch (Exception) { throw; }
    }
}
