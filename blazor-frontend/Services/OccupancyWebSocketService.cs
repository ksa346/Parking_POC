using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using BlazorParking.Models;

namespace BlazorParking.Services;

public class OccupancyWebSocketService : IDisposable
{
    private readonly string _backendUrl;
    private readonly ILogger<OccupancyWebSocketService> _logger;
    private readonly bool _enabled;
    private ClientWebSocket? _ws;
    private CancellationTokenSource? _cts;

    private static readonly JsonSerializerOptions _json = new() { PropertyNameCaseInsensitive = true };

    public event Action<OccupancyData>? OnOccupancyUpdate;
    public event Action<string>? OnStatusChange;

    public string Status { get; private set; } = "disconnected";
    public OccupancyData? Latest { get; private set; }

    public OccupancyWebSocketService(IConfiguration config, ILogger<OccupancyWebSocketService> logger)
    {
        _backendUrl = config["BackendUrl"] ?? "http://localhost:8000";
        _logger = logger;
        _enabled = bool.TryParse(config["EnableOccupancyWebSocket"], out var enabled) && enabled;

        if (_enabled)
        {
            _ = ConnectLoopAsync();
        }
        else
        {
            SetStatus("disabled");
            _logger.LogInformation("Occupancy websocket is disabled (EnableOccupancyWebSocket=false).");
        }
    }

    private async Task ConnectLoopAsync()
    {
        _cts = new CancellationTokenSource();
        var ct = _cts.Token;
        var delay = 1000;

        while (!ct.IsCancellationRequested)
        {
            try
            {
                _ws?.Dispose();
                _ws = new ClientWebSocket();

                var wsUrl = _backendUrl
                    .Replace("https://", "wss://")
                    .Replace("http://", "ws://")
                    .TrimEnd('/') + "/api/ws/occupancy";

                await _ws.ConnectAsync(new Uri(wsUrl), ct);
                SetStatus("connected");
                delay = 1000;
                await ReceiveLoopAsync(ct);
            }
            catch (OperationCanceledException) { break; }
            catch (Exception ex)
            {
                _logger.LogWarning("WebSocket error: {Msg}", ex.Message);
                SetStatus("disconnected");
                try { await Task.Delay(Math.Min(delay, 30_000), ct); }
                catch (OperationCanceledException) { break; }
                delay = Math.Min(delay * 2, 30_000);
            }
        }
    }

    private async Task ReceiveLoopAsync(CancellationToken ct)
    {
        var buffer = new byte[65536];
        while (_ws?.State == WebSocketState.Open && !ct.IsCancellationRequested)
        {
            var result = await _ws.ReceiveAsync(buffer, ct);
            if (result.MessageType == WebSocketMessageType.Close) break;

            var json = Encoding.UTF8.GetString(buffer, 0, result.Count);
            try
            {
                var data = JsonSerializer.Deserialize<OccupancyData>(json, _json);
                if (data != null)
                {
                    Latest = data;
                    OnOccupancyUpdate?.Invoke(data);
                }
            }
            catch (Exception ex) { _logger.LogWarning("WS parse error: {Msg}", ex.Message); }
        }
    }

    private void SetStatus(string s)
    {
        Status = s;
        OnStatusChange?.Invoke(s);
    }

    public void Dispose()
    {
        _cts?.Cancel();
        _ws?.Dispose();
    }
}
