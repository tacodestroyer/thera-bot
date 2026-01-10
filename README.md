# Eve Online Thera Wormhole Discord Bot 🌀

A Discord bot that monitors [Eve-Scout](https://www.eve-scout.com/) for Thera wormhole connections and notifies your corporation when good routes are available to your destinations.

## Features

- 🔍 **Automatic Monitoring**: Periodically checks Eve-Scout API for Thera connections
- 📍 **Route Calculation**: Uses ESI to calculate jump distances from your HQ to destinations via Thera
- 🔔 **Smart Notifications**: Only alerts when routes meet your jump threshold criteria
- 📊 **Detailed Information**: Includes wormhole signatures, size, type, and remaining lifetime
- ⏰ **Cooldown System**: Prevents notification spam for the same connection
- 🎮 **Interactive Commands**: Manual checks, status display, and connection listing

## Example Notification

```
🌀 Thera Connection to Jita!

@everyone

A connection to Jita is available via Thera!

3j from HQ to Thera exit
4j from Thera exit to Jita
Total: 7 jumps

📍 Thera Exit System
🟢 Pator
Region: Heimatar

🔑 Signatures
Thera side: CRS-680
K-space side: YEI-976

📊 Wormhole Info
Type: Q063
Size: 🔷 Medium
✅ ~16h remaining
```

## Prerequisites

- Python 3.8 or higher
- A Discord Bot Token
- A Discord server with a channel for notifications

## Installation

### 1. Clone or Download

```bash
git clone <repository-url>
cd eve-thera-bot
```

Or download and extract the files to a folder.

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Create a Discord Bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application" and give it a name
3. Go to the "Bot" section and click "Add Bot"
4. Copy the **Bot Token** (you'll need this for config)
5. Under "Privileged Gateway Intents", enable:
   - Message Content Intent
6. Go to "OAuth2" > "URL Generator"
7. Select scopes: `bot`
8. Select permissions: `Send Messages`, `Embed Links`, `Read Message History`
9. Copy the generated URL and use it to invite the bot to your server

### 4. Get Your Channel ID

1. In Discord, go to User Settings > Advanced > Enable "Developer Mode"
2. Right-click the channel where you want notifications
3. Click "Copy ID"

### 5. Configure the Bot

Edit `config.yaml` with your settings:

```yaml
discord:
  bot_token: "YOUR_DISCORD_BOT_TOKEN_HERE"
  channel_id: "YOUR_CHANNEL_ID_HERE"
  mention_everyone: true

hq_system:
  name: "YourHQSystem"
  id: 30000142  # Your HQ system ID

destinations:
  - name: "Jita"
    system_id: 30000142
    max_jumps: 10
```

### 6. Run the Bot

```bash
python thera_bot.py
```

## Configuration Options

### Discord Settings

| Option | Description |
|--------|-------------|
| `bot_token` | Your Discord bot token |
| `channel_id` | Channel ID for notifications |
| `mention_everyone` | Whether to use @everyone (true/false) |
| `mention_role_id` | Optional: Specific role ID to mention instead |

### HQ System

| Option | Description |
|--------|-------------|
| `name` | Display name of your HQ system |
| `id` | EVE system ID of your HQ |

### Destinations

Each destination has:
| Option | Description |
|--------|-------------|
| `name` | Display name of the destination |
| `system_id` | EVE system ID |
| `max_jumps` | Maximum total jumps (HQ→Thera + Thera→Dest) to trigger alert |

### Wormhole Filter

| Option | Description |
|--------|-------------|
| `min_wormhole_size` | Minimum wormhole size: `small`, `medium`, `large`, `xlarge`, `capital` |

### Polling

| Option | Description |
|--------|-------------|
| `interval_seconds` | How often to check (default: 300 = 5 minutes) |
| `cooldown_seconds` | Don't re-alert same connection within this time (default: 3600 = 1 hour) |

### Route Calculation

| Option | Description |
|--------|-------------|
| `preference` | Route type: `shortest`, `secure`, `insecure` |

## Bot Commands

| Command | Description |
|---------|-------------|
| `!thera check` | Manually check for connections now |
| `!thera status` | Show bot configuration and status |
| `!thera list` | List all current Thera connections |
| `!thera help` | Show help information |

## Finding System IDs

You can find EVE system IDs using:

1. **ESI Swagger UI**: https://esi.evetech.net/ui/#/Search/get_search
2. **zKillboard**: Look at the URL when viewing a system
3. **Dotlan**: System IDs are in the URLs

### Common Trade Hub IDs

| System | ID |
|--------|-----|
| Jita | 30000142 |
| Amarr | 30002187 |
| Dodixie | 30002659 |
| Rens | 30002510 |
| Hek | 30002053 |

## Wormhole Size Reference

| Size | Ships That Fit |
|------|----------------|
| Small | Frigates, Destroyers |
| Medium | Cruisers, Battlecruisers |
| Large | Battleships |
| XLarge | Capitals (not Supers) |
| Capital | All ships including Supers |

## Running as a Service

### Linux (systemd)

Create `/etc/systemd/system/thera-bot.service`:

```ini
[Unit]
Description=Eve Thera Discord Bot
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/eve-thera-bot
ExecStart=/usr/bin/python3 thera_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl enable thera-bot
sudo systemctl start thera-bot
```

### Windows (Task Scheduler)

1. Open Task Scheduler
2. Create Basic Task
3. Set trigger to "When the computer starts"
4. Action: Start a program
5. Program: `python` or full path to python.exe
6. Arguments: `thera_bot.py`
7. Start in: Path to the bot folder

## Troubleshooting

### Bot doesn't respond to commands
- Ensure Message Content Intent is enabled in Discord Developer Portal
- Check that the bot has permissions in the channel

### No notifications being sent
- Verify the channel ID is correct
- Check that destinations are configured with reasonable `max_jumps` values
- Look at the log file for errors

### "Invalid Discord bot token" error
- Double-check your bot token in config.yaml
- Regenerate the token in Discord Developer Portal if needed

### Route calculation fails
- Some systems (like wormhole space) can't have routes calculated
- The bot automatically skips these

## Data Sources

- **Wormhole Data**: [Eve-Scout Public API](https://www.eve-scout.com/)
- **Route Calculation**: [EVE Swagger Interface (ESI)](https://esi.evetech.net/)

## License

MIT License - Feel free to modify and distribute.

## Credits

- [Eve-Scout](https://www.eve-scout.com/) for providing the Thera connection data
- [Signal Cartel](https://www.eve-scout.com/signal-cartel/) for maintaining Eve-Scout
- CCP Games for EVE Online and the ESI API

## Contributing

Contributions are welcome! Feel free to submit issues and pull requests.

---

*Fly safe! o7*
