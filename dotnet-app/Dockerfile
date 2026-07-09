# ── Build stage ───────────────────────────────────────────────────────────────
FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src
COPY ChatWithData.Web.csproj .
RUN dotnet restore
COPY . .
RUN dotnet publish -c Release -o /app

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM mcr.microsoft.com/dotnet/aspnet:8.0 AS runtime
WORKDIR /app
COPY --from=build /app .

# Bind to all interfaces inside the container; compose maps it to the host.
ENV ASPNETCORE_URLS=http://0.0.0.0:5080
EXPOSE 5080

ENTRYPOINT ["dotnet", "ChatWithData.Web.dll"]
