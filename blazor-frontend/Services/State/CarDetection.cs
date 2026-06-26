using System.Net.Http.Json;
using System.Text.Json;
using BlazorParking.Models;

namespace BlazorParking.Services.State;

public class CarDetection
{
    public int ParkingSpots { get; set; }
    public int ReservedSpots { get; set; }
    public bool EnableReservation { get; set; }
    public int TotalLotCount { get; set; } = 100;
    public List<ActivityLog> ActivityLogs = [];
}