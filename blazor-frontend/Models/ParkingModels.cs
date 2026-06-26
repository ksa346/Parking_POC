using System.Text.Json.Serialization;

namespace BlazorParking.Models;

public class OccupancyData
{
    [JsonPropertyName("occupied_spots")]
    public int OccupiedSpots { get; set; }

    [JsonPropertyName("available_spots")]
    public int AvailableSpots { get; set; }

    [JsonPropertyName("total_spots")]
    public int TotalSpots { get; set; }

    [JsonPropertyName("occupancy_percent")]
    public double OccupancyPercent { get; set; }

    [JsonPropertyName("zones")]
    public List<ZoneData> Zones { get; set; } = [];

    [JsonPropertyName("timestamp")]
    public string Timestamp { get; set; } = "";

    [JsonPropertyName("detection_method")]
    public string DetectionMethod { get; set; } = "";

    [JsonPropertyName("confidence")]
    public string Confidence { get; set; } = "";
}

public class ZoneData
{
    [JsonPropertyName("zone_id")]
    public string ZoneId { get; set; } = "";

    [JsonPropertyName("occupied")]
    public int Occupied { get; set; }

    [JsonPropertyName("total")]
    public int Total { get; set; }

    [JsonPropertyName("double_parked")]
    public int DoubleParked { get; set; }
}

public class StatsData
{
    [JsonPropertyName("peak_hour")]
    public int PeakHour { get; set; }

    [JsonPropertyName("today_average_occupancy")]
    public double TodayAverageOccupancy { get; set; }
}

public class HistoryEntry
{
    [JsonPropertyName("timestamp")]
    public string Timestamp { get; set; } = "";

    [JsonPropertyName("occupied")]
    public int Occupied { get; set; }

    [JsonPropertyName("total")]
    public int Total { get; set; }
}

public class ChatMessage
{
    [JsonPropertyName("role")]
    public string Role { get; set; } = "";

    [JsonPropertyName("content")]
    public string Content { get; set; } = "";
}

public class ChatRequest
{
    [JsonPropertyName("message")]
    public string Message { get; set; } = "";

    [JsonPropertyName("history")]
    public List<ChatMessage> History { get; set; } = [];
}

public class ChatResponse
{
    [JsonPropertyName("reply")]
    public string Reply { get; set; } = "";
}

public class VideoCountEntry
{
    [JsonPropertyName("second")]
    public int Second { get; set; }

    [JsonPropertyName("count")]
    public int Count { get; set; }
}

public class UploadApplyResponse
{
    [JsonPropertyName("message")]
    public string Message { get; set; } = "";

    [JsonPropertyName("error")]
    public string Error { get; set; } = "";

    [JsonPropertyName("video_path")]
    public string VideoPath { get; set; } = "";

    [JsonPropertyName("filename")]
    public string Filename { get; set; } = "";
}

public class VideoCountsResponse
{
    [JsonPropertyName("video_path")]
    public string VideoPath { get; set; } = "";

    [JsonPropertyName("model_path")]
    public string ModelPath { get; set; } = "";

    [JsonPropertyName("duration_seconds")]
    public double DurationSeconds { get; set; }

    [JsonPropertyName("interval_seconds")]
    public int IntervalSeconds { get; set; }

    [JsonPropertyName("confidence_threshold")]
    public double ConfidenceThreshold { get; set; }

    [JsonPropertyName("count")]
    public int? Count { get; set; }

    [JsonPropertyName("counts")]
    public List<VideoCountEntry> Counts { get; set; } = [];
    
    [JsonPropertyName("batch")]
    public bool Batch { get; set; }

    [JsonPropertyName("total")]
    public int? Total { get; set; }

    [JsonPropertyName("results")]
    public List<VideoCountsResponse>? Results { get; set; }

    // Per-video error (present inside batch results when a video fails)
    [JsonPropertyName("error")]
    public string? Error { get; set; }
}

public class EntryExitResult
{
    [JsonPropertyName("entry_video_path")]
    public string EntryVideoPath { get; set; } = "";

    [JsonPropertyName("exit_video_path")]
    public string ExitVideoPath { get; set; } = "";

    [JsonPropertyName("model_path")]
    public string ModelPath { get; set; } = "";

    [JsonPropertyName("confidence_threshold")]
    public double ConfidenceThreshold { get; set; }

    [JsonPropertyName("frame_stride")]
    public int FrameStride { get; set; }

    [JsonPropertyName("entered_count")]
    public int EnteredCount { get; set; }

    [JsonPropertyName("exited_count")]
    public int ExitedCount { get; set; }

    [JsonPropertyName("entry")]
    public EntryExitStreamResult? Entry { get; set; }

    [JsonPropertyName("exit")]
    public EntryExitStreamResult? Exit { get; set; }

    [JsonPropertyName("saved_output_dir")]
    public string? SavedOutputDir { get; set; }

    [JsonPropertyName("processed_output_dir")]
    public string? ProcessedOutputDir { get; set; }

    [JsonPropertyName("processed_entry_video_path")]
    public string? ProcessedEntryVideoPath { get; set; }

    [JsonPropertyName("processed_exit_video_path")]
    public string? ProcessedExitVideoPath { get; set; }

    [JsonPropertyName("processed_entry_video_url")]
    public string? ProcessedEntryVideoUrl { get; set; }

    [JsonPropertyName("processed_exit_video_url")]
    public string? ProcessedExitVideoUrl { get; set; }
}

public class EntryExitStreamResult
{
    [JsonPropertyName("video_path")]
    public string VideoPath { get; set; } = "";

    [JsonPropertyName("duration_seconds")]
    public double? DurationSeconds { get; set; }

    [JsonPropertyName("fps")]
    public double? Fps { get; set; }

    [JsonPropertyName("frame_count")]
    public int FrameCount { get; set; }

    [JsonPropertyName("frame_stride")]
    public int FrameStride { get; set; }

    [JsonPropertyName("count")]
    public int Count { get; set; }

    [JsonPropertyName("tripwire")]
    public EntryExitTripwireResult? Tripwire { get; set; }

    [JsonPropertyName("processed_video_path")]
    public string? ProcessedVideoPath { get; set; }

    [JsonPropertyName("processed_video_url")]
    public string? ProcessedVideoUrl { get; set; }
}

public class EntryExitTripwireResult
{
    [JsonPropertyName("x1")]
    public double X1 { get; set; }

    [JsonPropertyName("y1")]
    public double Y1 { get; set; }

    [JsonPropertyName("x2")]
    public double X2 { get; set; }

    [JsonPropertyName("y2")]
    public double Y2 { get; set; }

    [JsonPropertyName("deadband_px")]
    public double DeadbandPx { get; set; }
}

public class LiveCountEvent
{
    [JsonPropertyName("event")]
    public string Event { get; set; } = "";

    [JsonPropertyName("seq")]
    public int Seq { get; set; }

    [JsonPropertyName("timestamp")]
    public string Timestamp { get; set; } = "";

    [JsonPropertyName("count")]
    public int Count { get; set; }

    [JsonPropertyName("message")]
    public string? Message { get; set; }
}

public class ActivityLog
{
    public DateTime TimeStamp {get; set;}
    public string Type {get; set;} = string.Empty;
    public string Details { get; set; } = string.Empty;
}

public class LiveCountsRequest
{
    [JsonPropertyName("streams")]
    public List<LiveCountStreamRequest> Streams { get; set; } = new();
}

public class LiveCountStreamRequest
{
    [JsonPropertyName("stream_id")]
    public string StreamId { get; set; } = "";

    [JsonPropertyName("source")]
    public string Source { get; set; } = "";

    [JsonPropertyName("conf")]
    public double Conf { get; set; } = 0.5;

    [JsonPropertyName("iou")]
    public double Iou { get; set; } = 0.45;

    [JsonPropertyName("regions")]
    public LiveCountRegions Regions { get; set; } = new();
}

public class LiveCountRegions
{
    [JsonPropertyName("region_left")]
    public List<int[]> RegionLeft { get; set; } = new();

    [JsonPropertyName("region_right")]
    public List<int[]> RegionRight { get; set; } = new();
}

public class LiveCountStreamResponse
{
    [JsonPropertyName("stream_id")]
    public string StreamId { get; set; } = "";

    [JsonPropertyName("count")]
    public int Count { get; set; }

    [JsonPropertyName("error")]
    public string? Error { get; set; }
}

public class VideoCountsRequest
{
    public List<string> VideoPaths { get; set; } = new();
    public double ConfidenceThreshold { get; set; }
    public int IntervalSeconds { get; set; }
}