using System.Net.Http.Json;
using System.Text.Json;
using BlazorParking.Models;

namespace BlazorParking.Services.State;

public class ConfigurationState
{
    public int ParkingSpots { get; set; }
    public int ReservedSpots { get; set; }
    public int TotalLotCount { get; set; } = 165;
    public int GeneralSpots { get; set; } = 10;
    public int AdaSpots { get; set; } = 10;
    public int EvSpots { get; set; } = 10;
    public List<ActivityLog> ActivityLogs = [];
}

public class ParkingReservation
{
    public int ReservedSpots { get; set; }
    public DateTime? StartDate { get; set; }
    public DateTime? EndDate { get; set; }
    public TimeOnly? StartTime { get; set; }
    public TimeOnly? EndTime { get; set; }
}