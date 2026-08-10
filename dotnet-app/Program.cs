// Chat with Data — .NET presentation layer
// -----------------------------------------
// Responsibilities (per the To-Be architecture):
//   * Serve the user interface (Standard + Variance).
//   * Own the per-user session (a cookie here; Windows Auth / AD later).
//   * Forward all data work to the Python micro-service over the internal
//     network, attaching the session id as the X-Session-Id header.
//
// The .NET app holds NO analytical logic — it is purely presentation + session.
// All analysis (the two-call pipeline, executor, prompts) lives in Python.

using System.Net.Http.Headers;
using Microsoft.AspNetCore.Authentication.Negotiate;

var builder = WebApplication.CreateBuilder(args);

// Base URL of the Python micro-service. Override via PythonService__BaseUrl
// (env var) or appsettings.json. Defaults to the local dev port.
var pythonBaseUrl = builder.Configuration["PythonService:BaseUrl"] ?? "http://localhost:8000";

// Authentication scaffold. Local/dev: disabled (passthrough). QA/PROD: enabled,
// using Windows Authentication against Active Directory. Toggle via Auth:Enabled.
var authEnabled = builder.Configuration.GetValue<bool>("Auth:Enabled");
if (authEnabled)
{
    builder.Services
        .AddAuthentication(NegotiateDefaults.AuthenticationScheme)
        .AddNegotiate();
    builder.Services.AddAuthorization();
}

builder.Services.AddHttpClient("python", client =>
{
    client.BaseAddress = new Uri(pythonBaseUrl);
    client.Timeout = TimeSpan.FromMinutes(3); // LLM calls can be slow
});

// Allow uploads up to ~60 MB (matches the Python service's 50 MB cap + overhead).
const long MaxUploadBytes = 60L * 1024 * 1024;
builder.Services.Configure<Microsoft.AspNetCore.Http.Features.FormOptions>(o =>
{
    o.MultipartBodyLengthLimit = MaxUploadBytes;
});
builder.WebHost.ConfigureKestrel(o => o.Limits.MaxRequestBodySize = MaxUploadBytes);

// Optional shared secret sent to the Python service on every proxied request
// (PythonService:InternalSecret). Pair with INTERNAL_API_SECRET in the Python
// .env so nothing on the network can talk to Python while it lives on a
// separate machine. Empty = disabled.
var internalSecret = builder.Configuration["PythonService:InternalSecret"] ?? "";

// Dev identity: when Windows Authentication is off (local testing), requests
// still need a stable username for per-user keys/usage. Auth:DevUsername
// overrides it (useful to simulate another user or a non-admin); otherwise the
// machine's logged-in username is used. With Windows Auth ON, the real
// authenticated name always wins.
var devUsername = builder.Configuration["Auth:DevUsername"] ?? "";

var app = builder.Build();

const string SidCookie = "cwd_sid";

string GetUsername(HttpContext ctx)
{
    var user = ctx.User?.Identity?.Name;
    if (!string.IsNullOrEmpty(user)) return user;                 // Windows Auth
    if (!string.IsNullOrEmpty(devUsername)) return devUsername;   // dev override
    return Environment.UserName;                                  // machine user
}

// Resolve (or create) the per-user session id and ensure the cookie is set.
string GetOrCreateSessionId(HttpContext ctx)
{
    if (ctx.Request.Cookies.TryGetValue(SidCookie, out var existing) && !string.IsNullOrWhiteSpace(existing))
        return existing;

    var sid = Guid.NewGuid().ToString("N");
    ctx.Response.Cookies.Append(SidCookie, sid, new CookieOptions
    {
        HttpOnly = true,
        SameSite = SameSiteMode.Lax,
        Secure = ctx.Request.IsHttps,
        MaxAge = TimeSpan.FromHours(12),
    });
    return sid;
}

HttpRequestMessage NewRequest(HttpContext ctx, HttpMethod method, string path, string sid)
{
    var req = new HttpRequestMessage(method, path);
    req.Headers.Add("X-Session-Id", sid);
    // Always identify the user (Windows Auth name, or the dev fallback) —
    // per-user API keys and usage tracking key off this header.
    req.Headers.Add("X-User", GetUsername(ctx));
    if (!string.IsNullOrEmpty(internalSecret))
        req.Headers.Add("X-Internal-Auth", internalSecret);
    return req;
}

// Copy a Python JSON/file response back to the browser verbatim.
async Task RelayResponse(HttpContext ctx, HttpResponseMessage upstream)
{
    ctx.Response.StatusCode = (int)upstream.StatusCode;

    if (upstream.Content.Headers.ContentType is { } ct)
        ctx.Response.ContentType = ct.ToString();

    if (upstream.Content.Headers.ContentDisposition is { } cd)
        ctx.Response.Headers["Content-Disposition"] = cd.ToString();

    await upstream.Content.CopyToAsync(ctx.Response.Body);
}

// ── Auth middleware (only when enabled) ────────────────────────────────────────
if (authEnabled)
{
    app.UseAuthentication();
    app.UseAuthorization();
}

// ── UI ────────────────────────────────────────────────────────────────────────
app.UseDefaultFiles();   // serve wwwroot/index.html at "/"
app.UseStaticFiles();

// ── Proxy: file uploads (multipart) ────────────────────────────────────────────
async Task ProxyUpload(HttpContext ctx, IHttpClientFactory factory, string targetPath)
{
    var sid = GetOrCreateSessionId(ctx);
    var client = factory.CreateClient("python");

    using var form = new MultipartFormDataContent();

    if (ctx.Request.HasFormContentType)
    {
        var incoming = await ctx.Request.ReadFormAsync();

        foreach (var file in incoming.Files)
        {
            var sc = new StreamContent(file.OpenReadStream());
            if (!string.IsNullOrEmpty(file.ContentType))
                sc.Headers.ContentType = new MediaTypeHeaderValue(file.ContentType);
            form.Add(sc, file.Name, file.FileName);
        }

        foreach (var field in incoming)
            foreach (var val in field.Value)
                form.Add(new StringContent(val ?? string.Empty), field.Key);
    }

    using var req = NewRequest(ctx, HttpMethod.Post, targetPath, sid);
    req.Content = form;
    using var resp = await client.SendAsync(req, HttpCompletionOption.ResponseHeadersRead);
    await RelayResponse(ctx, resp);
}

var dataEndpoints = new List<RouteHandlerBuilder>();

dataEndpoints.Add(app.MapPost("/upload/standard", (HttpContext ctx, IHttpClientFactory f) =>
    ProxyUpload(ctx, f, "/api/upload/standard")));

dataEndpoints.Add(app.MapPost("/upload/variance/{slot}", (HttpContext ctx, IHttpClientFactory f, string slot) =>
    ProxyUpload(ctx, f, $"/api/upload/variance/{slot}")));

// ── Proxy: JSON POSTs (ask / clear) ─────────────────────────────────────────────
async Task ProxyJson(HttpContext ctx, IHttpClientFactory factory, string targetPath)
{
    targetPath += ctx.Request.QueryString.Value;   // forward ?period=... etc.
    var sid = GetOrCreateSessionId(ctx);
    var client = factory.CreateClient("python");

    using var reader = new StreamReader(ctx.Request.Body);
    var body = await reader.ReadToEndAsync();

    using var req = NewRequest(ctx, HttpMethod.Post, targetPath, sid);
    req.Content = new StringContent(body, System.Text.Encoding.UTF8, "application/json");
    using var resp = await client.SendAsync(req, HttpCompletionOption.ResponseHeadersRead);
    await RelayResponse(ctx, resp);
}

dataEndpoints.Add(app.MapPost("/ask",   (HttpContext ctx, IHttpClientFactory f) => ProxyJson(ctx, f, "/api/ask")));
dataEndpoints.Add(app.MapPost("/ask/refine", (HttpContext ctx, IHttpClientFactory f) => ProxyJson(ctx, f, "/api/ask/refine")));
dataEndpoints.Add(app.MapPost("/clear", (HttpContext ctx, IHttpClientFactory f) => ProxyJson(ctx, f, "/api/clear")));
dataEndpoints.Add(app.MapPost("/export/conversation", (HttpContext ctx, IHttpClientFactory f) => ProxyJson(ctx, f, "/api/export/conversation")));

// ── Proxy: file downloads (GET) ──────────────────────────────────────────────────
async Task ProxyGet(HttpContext ctx, IHttpClientFactory factory, string targetPath)
{
    targetPath += ctx.Request.QueryString.Value;   // forward ?period=... etc.
    var sid = GetOrCreateSessionId(ctx);
    var client = factory.CreateClient("python");

    using var req = NewRequest(ctx, HttpMethod.Get, targetPath, sid);
    using var resp = await client.SendAsync(req, HttpCompletionOption.ResponseHeadersRead);
    await RelayResponse(ctx, resp);
}

dataEndpoints.Add(app.MapGet("/export/last_result",  (HttpContext ctx, IHttpClientFactory f) => ProxyGet(ctx, f, "/api/export/last_result")));
dataEndpoints.Add(app.MapGet("/export/debug_result", (HttpContext ctx, IHttpClientFactory f) => ProxyGet(ctx, f, "/api/export/debug_result")));

// ── Rollout batch: per-user API keys, usage dashboards, admin, model select ──
dataEndpoints.Add(app.MapGet ("/api/user/check_key",       (HttpContext ctx, IHttpClientFactory f) => ProxyGet (ctx, f, "/api/user/check_key")));
dataEndpoints.Add(app.MapPost("/api/user/save_key",        (HttpContext ctx, IHttpClientFactory f) => ProxyJson(ctx, f, "/api/user/save_key")));
dataEndpoints.Add(app.MapGet ("/api/user/get_key",         (HttpContext ctx, IHttpClientFactory f) => ProxyGet (ctx, f, "/api/user/get_key")));
dataEndpoints.Add(app.MapGet ("/api/user/usage/summary",   (HttpContext ctx, IHttpClientFactory f) => ProxyGet (ctx, f, "/api/user/usage/summary")));
dataEndpoints.Add(app.MapGet ("/api/user/usage/chart",     (HttpContext ctx, IHttpClientFactory f) => ProxyGet (ctx, f, "/api/user/usage/chart")));
dataEndpoints.Add(app.MapGet ("/api/user/usage/history",   (HttpContext ctx, IHttpClientFactory f) => ProxyGet (ctx, f, "/api/user/usage/history")));
dataEndpoints.Add(app.MapGet ("/api/admin/check",          (HttpContext ctx, IHttpClientFactory f) => ProxyGet (ctx, f, "/api/admin/check")));
dataEndpoints.Add(app.MapGet ("/api/admin/users/list",     (HttpContext ctx, IHttpClientFactory f) => ProxyGet (ctx, f, "/api/admin/users/list")));
dataEndpoints.Add(app.MapGet ("/api/admin/usage/summary",  (HttpContext ctx, IHttpClientFactory f) => ProxyGet (ctx, f, "/api/admin/usage/summary")));
dataEndpoints.Add(app.MapGet ("/api/admin/usage/by_user",  (HttpContext ctx, IHttpClientFactory f) => ProxyGet (ctx, f, "/api/admin/usage/by_user")));
dataEndpoints.Add(app.MapGet ("/api/admin/usage/by_model", (HttpContext ctx, IHttpClientFactory f) => ProxyGet (ctx, f, "/api/admin/usage/by_model")));
dataEndpoints.Add(app.MapGet ("/api/admin/models/available",(HttpContext ctx, IHttpClientFactory f) => ProxyGet (ctx, f, "/api/admin/models/available")));
dataEndpoints.Add(app.MapGet ("/api/admin/models/current", (HttpContext ctx, IHttpClientFactory f) => ProxyGet (ctx, f, "/api/admin/models/current")));
dataEndpoints.Add(app.MapPost("/api/admin/models/select",  (HttpContext ctx, IHttpClientFactory f) => ProxyJson(ctx, f, "/api/admin/models/select")));
dataEndpoints.Add(app.MapGet ("/api/admin/models/history", (HttpContext ctx, IHttpClientFactory f) => ProxyGet (ctx, f, "/api/admin/models/history")));

// When auth is enabled (QA/PROD), require an authenticated user on every data
// endpoint. In local/dev (Auth:Enabled=false) these stay open for easy testing.
if (authEnabled)
{
    foreach (var ep in dataEndpoints)
        ep.RequireAuthorization();
}

// ── Health (checks both layers) ───────────────────────────────────────────────────
app.MapGet("/healthz", async (IHttpClientFactory f) =>
{
    var client = f.CreateClient("python");
    try
    {
        var resp = await client.GetAsync("/health");
        var body = await resp.Content.ReadAsStringAsync();
        return Results.Content($"{{\"dotnet\":\"ok\",\"python\":{body}}}", "application/json");
    }
    catch (Exception ex)
    {
        return Results.Content($"{{\"dotnet\":\"ok\",\"python\":\"unreachable: {ex.Message}\"}}", "application/json");
    }
});

app.Run();
