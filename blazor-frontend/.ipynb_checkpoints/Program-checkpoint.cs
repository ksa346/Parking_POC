using BlazorParking.Services;
using BlazorParking.Services.State;
using Microsoft.Extensions.FileProviders;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddRazorPages();
builder.Services.AddServerSideBlazor();

var backendUrl = builder.Configuration["BackendUrl"] ?? "http://localhost:8000";

builder.Services.AddHttpClient<ParkingService>(client =>
{
    client.BaseAddress = new Uri(backendUrl);
    client.Timeout = TimeSpan.FromMinutes(30);
});

builder.Services.AddSingleton<OccupancyWebSocketService>();
builder.Services.AddScoped<LiveCountsWebSocketService>();
builder.Services.AddScoped<EntryExitState>();
builder.Services.AddScoped<ConfigurationState>();
builder.Services.AddScoped<GarageSelectionState>();
builder.Services.AddScoped<ParkingReservation>();

var app = builder.Build();

if (!app.Environment.IsDevelopment())
    app.UseExceptionHandler("/Error");

app.UseStaticFiles();
// app.UseStaticFiles(
//     new StaticFileOptions
//     {
//         FileProvider = new PhysicalFileProvider(Path.Combine(builder.Environment.ContentRootPath, "data", "entry_exit_images")),
//         RequestPath = "/entry-exit-images"
//     }
// );
// app.UseStaticFiles(
//     new StaticFileOptions
//     {
//         FileProvider = new PhysicalFileProvider(Path.Combine(builder.Environment.ContentRootPath, "data", "evidance")),
//         RequestPath = "/evidence"
//     }
// );
var entryExitImagesPath = Path.Combine(builder.Environment.ContentRootPath, "data", "entry_exit_images");
if (Directory.Exists(entryExitImagesPath))
{
    app.UseStaticFiles(
        new StaticFileOptions
        {
            FileProvider = new PhysicalFileProvider(entryExitImagesPath),
            RequestPath = "/entry-exit-images"
        }
    );
}
 
var evidancePath = Path.Combine(builder.Environment.ContentRootPath, "data", "evidance");
if (Directory.Exists(evidancePath))
{
    app.UseStaticFiles(
        new StaticFileOptions
        {
            FileProvider = new PhysicalFileProvider(evidancePath),
            RequestPath = "/evidance"
        }
    );
}
app.UseRouting();
app.MapBlazorHub();
app.MapFallbackToPage("/_Host");

app.Run();
