namespace BlazorParking.Services.State;

public class GarageSelectionState
{
    private static readonly IReadOnlyList<string> AvailableGarages =
    [
        "Cornerstone Garage",
        "Medical Center",
        "Medical Rose Street"
    ];

    private static readonly IReadOnlyList<string> AvailableZones =
    [
        "Level 1 East",
        "Level 1 West",
        "Level 2 East",
        "Level 2 West"
    ];

    public IReadOnlyList<string> Garages => AvailableGarages;
    public IReadOnlyList<string> Zones => AvailableZones;

    public string SelectedGarage { get; private set; } = AvailableGarages[0];
    public string SelectedZone { get; private set; } = AvailableZones[0];

    public event Action? OnChange;

    public void SetSelectedGarage(string? garage)
    {
        if (string.IsNullOrWhiteSpace(garage) || garage == SelectedGarage || !AvailableGarages.Contains(garage))
        {
            return;
        }

        SelectedGarage = garage;
        OnChange?.Invoke();
    }

    public void SetSelectedZone(string? zone)
    {
        if (string.IsNullOrWhiteSpace(zone) || zone == SelectedZone || !AvailableZones.Contains(zone))
        {
            return;
        }

        SelectedZone = zone;
        OnChange?.Invoke();
    }
}