using System.Net.Http.Json;
using System.Text.Json;
using BlazorParking.Models;

namespace BlazorParking.Services.State;

public class EntryExitState
{
    public int EntryCount{get; set;}
    public int ExitCount{get; set;}
    public string? EntryVideoPath { get; set; } = "rtsp://ukcamviz:qUoTuRNytxe6EH@10.29.18.175/0/profile2/media.smp"; 
    public string? ExitVideoPath { get; set; } = "rtsp://ukcamviz:qUoTuRNytxe6EH@10.29.18.171/axis-media/media.amp";
    public double Confidence { get; set; } = 0.9;
    public int FrameStride { get; set; } = 1;
    public int? EntryX1 { get; set; } = 450;
    public int? EntryX2 { get; set; } = 1600;
    public int? EntryY1 { get; set; } = 300;
    public int? EntryY2 { get; set; } = 300;
    public int? ExitX1 { get; set; } = 400;
    public int? ExitX2 { get; set; } = 1120;
    public int? ExitY1 { get; set; } = 250;
    public int? ExitY2 { get; set; } = 250;
    public int CurrentAvailable {get; set;}
    public int EEOccupied { get; set; } = 20;
    public int SpotOccupied { get; set; }
    public int GeneralOccupied { get; set; }
    public int AdaOccupied { get; set; }
    public int EvOccupied { get; set; }
    public int TotalEntered { get; set; }
    public int TotalExited { get; set; }
    public int NetChange { get; set; }
    public string? ProcessedEntryVideoUrl { get; set; }
    public string? ProcessedEntryVideoFileName { get; set; }
    public string? ProcessedExitVideoUrl { get; set; }
    public string? ProcessedExitVideoFileName { get; set; }
    public List<ValidationRecord> Validations { get; set; } = [];
    public List<string> ImageFiles { get; set; } = [];
    public string? EntryEvidenceDir { get; set; }
    public string? ExitEvidenceDir { get; set; }
}

public record ValidationRecord(int Id, DateTime Time, string Frame, string FileName,
                        int Count, string DetectionType, string ValidationStatus);

public class DetectionReviewModel
{
    public string ImagePath { get; set; } = string.Empty;
    public string FrameName { get; set; } = string.Empty;
    public DateTime TimeStamp { get; set; }
    public string Direction { get; set; } = string.Empty;
    public int CountImapct { get; set; }
    public bool? IsCorrectDetection { get; set; }
    public string Comments { get; set; } = string.Empty;
}