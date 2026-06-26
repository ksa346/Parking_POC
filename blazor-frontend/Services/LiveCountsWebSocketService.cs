using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using BlazorParking.Models;

namespace BlazorParking.Services;

public class LiveCountsWebSocketService : IAsyncDisposable
{
    private readonly string _backendUrl;
    private readonly ILogger<LiveCountsWebSocketService> _logger;
    private ClientWebSocket? _ws;
    private CancellationTokenSource? _cts;

    private static readonly JsonSerializerOptions _json = new() { PropertyNameCaseInsensitive = true };

    public event Action<LiveCountEvent>? OnCountReceived;
    public event Action<string>? OnStatusChanged;
    public event Action<string>? OnError;

    public string Status { get; private set; } = "idle";

    public LiveCountsWebSocketService(IConfiguration config, ILogger<LiveCountsWebSocketService> logger)
    {
        _backendUrl = config["BackendUrl"] ?? "http://localhost:8000";
        _logger = logger;
    }

    public async Task StartAsync(string liveUrl, string modelPath = "base", int intervalSeconds = 10, double confidence = 0.5)
    {
        if (Status == "connected" || Status == "connecting") return;

        _cts?.Cancel();
        _cts = new CancellationTokenSource();
        var ct = _cts.Token;

        SetStatus("connecting");

        try
        {
            _ws?.Dispose();
            _ws = new ClientWebSocket();

            var wsUrl = _backendUrl
                .Replace("https://", "wss://")
                .Replace("http://", "ws://")
                .TrimEnd('/') + "/api/ws/live-counts";

            await _ws.ConnectAsync(new Uri(wsUrl), ct);

            var config = JsonSerializer.Serialize(new
            {
                live_url = liveUrl,
                model_path = modelPath,
                interval_seconds = intervalSeconds,
                confidence_threshold = confidence,
                save_annotated_frames = false
            });

            await _ws.SendAsync(Encoding.UTF8.GetBytes(config), WebSocketMessageType.Text, true, ct);
            SetStatus("connected");

            _ = ReceiveLoopAsync(ct);
        }
        catch (Exception ex)
        {
            _logger.LogError("LiveCounts WS connect error: {Msg}", ex.Message);
            SetStatus("idle");
            OnError?.Invoke(ex.Message);
        }
    }

    public async Task StopAsync()
    {
        _cts?.Cancel();
        if (_ws?.State == WebSocketState.Open)
        {
            try { await _ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "stopped", CancellationToken.None); }
            catch { }
        }
        SetStatus("idle");
    }

    private async Task ReceiveLoopAsync(CancellationToken ct)
    {
        var buffer = new byte[65536];
        try
        {
            while (_ws?.State == WebSocketState.Open && !ct.IsCancellationRequested)
            {
                var result = await _ws.ReceiveAsync(buffer, ct);
                if (result.MessageType == WebSocketMessageType.Close) break;

                var json = Encoding.UTF8.GetString(buffer, 0, result.Count);
                try
                {
                    var evt = JsonSerializer.Deserialize<LiveCountEvent>(json, _json);
                    if (evt == null) continue;

                    if (evt.Event == "error")
                    {
                        OnError?.Invoke(evt.Message ?? "Unknown error");
                        SetStatus("idle");
                        break;
                    }

                    if (evt.Event == "count")
                        OnCountReceived?.Invoke(evt);
                }
                catch (Exception ex) { _logger.LogWarning("LiveCounts parse error: {Msg}", ex.Message); }
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { _logger.LogError("LiveCounts receive error: {Msg}", ex.Message); }
        finally
        {
            if (Status == "connected")
                SetStatus("idle");
        }
    }

    private void SetStatus(string s)
    {
        Status = s;
        OnStatusChanged?.Invoke(s);
    }

    public async ValueTask DisposeAsync()
    {
        _cts?.Cancel();
        if (_ws?.State == WebSocketState.Open)
        {
            try { await _ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "disposed", CancellationToken.None); }
            catch { }
        }
        _ws?.Dispose();
        _cts?.Dispose();
    }
}
