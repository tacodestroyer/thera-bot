#!/usr/bin/env python3
"""
Eve Online Thera Wormhole Discord Bot

This bot monitors Eve-Scout's public API for Thera wormhole connections
and sends Discord notifications when good routes are available to
user-defined destinations.

Author: Kilo Code
License: MIT
"""

import asyncio
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import aiohttp
import discord
import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from discord.ext import commands

# Constants
THERA_SYSTEM_ID = 31000005
THERA_SYSTEM_NAME = "Thera"
EVE_SCOUT_API_URL = "https://api.eve-scout.com/v2/public/signatures"
ESI_ROUTE_URL = "https://esi.evetech.net/latest/route/{origin}/{destination}/"

# Wormhole size hierarchy (for filtering)
WORMHOLE_SIZES = {
    "small": 0,
    "medium": 1,
    "large": 2,
    "xlarge": 3,
    "capital": 4
}


class Config:
    """Configuration manager for the bot."""
    
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = Path(config_path)
        self.load()
    
    def load(self) -> None:
        """Load configuration from YAML file."""
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Configuration file not found: {self.config_path}\n"
                "Please copy config.yaml.example to config.yaml and configure it."
            )
        
        with open(self.config_path, 'r', encoding='utf-8') as f:
            self._config = yaml.safe_load(f)
        
        # Validate required fields
        self._validate()
    
    def _validate(self) -> None:
        """Validate configuration has required fields."""
        required = [
            ('discord', 'bot_token'),
            ('discord', 'channel_id'),
        ]
        
        for keys in required:
            obj = self._config
            for key in keys:
                if key not in obj:
                    raise ValueError(f"Missing required config: {'.'.join(keys)}")
                obj = obj[key]
        
        # Validate departure_systems (support both old hq_system and new departure_systems)
        if 'departure_systems' not in self._config and 'hq_system' not in self._config:
            raise ValueError("Missing required config: departure_systems (or legacy hq_system)")
        
        # If using new format, validate each departure system
        if 'departure_systems' in self._config:
            for i, dep in enumerate(self._config['departure_systems']):
                if 'name' not in dep:
                    raise ValueError(f"Missing 'name' in departure_systems[{i}]")
                if 'system_id' not in dep:
                    raise ValueError(f"Missing 'system_id' in departure_systems[{i}]")
    
    @property
    def bot_token(self) -> str:
        return self._config['discord']['bot_token']
    
    @property
    def channel_id(self) -> int:
        return int(self._config['discord']['channel_id'])
    
    @property
    def mention_everyone(self) -> bool:
        return self._config['discord'].get('mention_everyone', True)
    
    @property
    def mention_role_id(self) -> Optional[int]:
        role_id = self._config['discord'].get('mention_role_id')
        return int(role_id) if role_id else None
    
    @property
    def departure_systems(self) -> List[Dict]:
        """Get list of departure systems. Supports legacy hq_system config."""
        if 'departure_systems' in self._config:
            return self._config['departure_systems']
        # Legacy support: convert old hq_system to new format
        elif 'hq_system' in self._config:
            return [{
                'name': self._config['hq_system']['name'],
                'system_id': self._config['hq_system']['id']
            }]
        return []
    
    @property
    def destinations(self) -> List[Dict]:
        return self._config.get('destinations', [])
    
    @property
    def polling_interval(self) -> int:
        return self._config.get('polling', {}).get('interval_seconds', 300)
    
    @property
    def cooldown_seconds(self) -> int:
        return self._config.get('polling', {}).get('cooldown_seconds', 3600)
    
    @property
    def route_preference(self) -> str:
        return self._config.get('route', {}).get('preference', 'shortest')
    
    @property
    def log_level(self) -> str:
        return self._config.get('logging', {}).get('level', 'INFO')
    
    @property
    def log_file(self) -> str:
        return self._config.get('logging', {}).get('file', 'thera_bot.log')


class EveScoutClient:
    """Client for interacting with the Eve-Scout API."""
    
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
    
    async def get_thera_connections(self) -> List[Dict]:
        """
        Fetch all current Thera wormhole connections from Eve-Scout.
        
        Returns:
            List of wormhole connection dictionaries
        """
        try:
            async with self.session.get(EVE_SCOUT_API_URL) as response:
                if response.status != 200:
                    logging.error(f"Eve-Scout API returned status {response.status}")
                    return []
                
                signatures = await response.json()
                
                # Filter for Thera connections only
                thera_connections = []
                for sig in signatures:
                    # Check if this is a Thera connection (either in or out)
                    if sig.get('out_system_id') == THERA_SYSTEM_ID or sig.get('in_system_id') == THERA_SYSTEM_ID:
                        thera_connections.append(sig)
                
                logging.debug(f"Found {len(thera_connections)} Thera connections")
                return thera_connections
                
        except aiohttp.ClientError as e:
            logging.error(f"Error fetching Eve-Scout data: {e}")
            return []
        except Exception as e:
            logging.error(f"Unexpected error fetching Eve-Scout data: {e}")
            return []


class ESIClient:
    """Client for interacting with the EVE Swagger Interface (ESI)."""
    
    def __init__(self, session: aiohttp.ClientSession, route_preference: str = "shortest"):
        self.session = session
        self.route_preference = route_preference
        self._route_cache: Dict[Tuple[int, int], Tuple[int, datetime]] = {}
        self._cache_duration = timedelta(minutes=30)
    
    async def get_route_jumps(self, origin_id: int, destination_id: int) -> Optional[int]:
        """
        Calculate the number of jumps between two systems using ESI.
        
        Args:
            origin_id: Origin system ID
            destination_id: Destination system ID
            
        Returns:
            Number of jumps, or None if route cannot be calculated
        """
        # Check cache first
        cache_key = (origin_id, destination_id)
        if cache_key in self._route_cache:
            jumps, cached_at = self._route_cache[cache_key]
            if datetime.now() - cached_at < self._cache_duration:
                return jumps
        
        # Build URL with route preference
        url = ESI_ROUTE_URL.format(origin=origin_id, destination=destination_id)
        params = {}
        
        if self.route_preference == "secure":
            params['flag'] = 'secure'
        elif self.route_preference == "insecure":
            params['flag'] = 'insecure'
        # "shortest" is the default, no flag needed
        
        try:
            async with self.session.get(url, params=params) as response:
                if response.status == 404:
                    # No route exists (e.g., to/from wormhole space)
                    return None
                elif response.status != 200:
                    logging.warning(f"ESI route API returned status {response.status}")
                    return None
                
                route = await response.json()
                jumps = len(route) - 1  # Route includes origin, so subtract 1
                
                # Cache the result
                self._route_cache[cache_key] = (jumps, datetime.now())
                
                return jumps
                
        except aiohttp.ClientError as e:
            logging.error(f"Error calculating route: {e}")
            return None
        except Exception as e:
            logging.error(f"Unexpected error calculating route: {e}")
            return None


class TheraConnection:
    """Represents a processed Thera wormhole connection."""
    
    def __init__(self, raw_data: Dict):
        self.raw = raw_data
        self.id = raw_data.get('id')
        
        # Determine which side is Thera and which is the k-space exit
        if raw_data.get('out_system_id') == THERA_SYSTEM_ID:
            # Thera is the "out" system, k-space is "in"
            self.thera_signature = raw_data.get('out_signature')
            self.exit_system_id = raw_data.get('in_system_id')
            self.exit_system_name = raw_data.get('in_system_name')
            self.exit_signature = raw_data.get('in_signature')
            self.exit_region = raw_data.get('in_region_name')
            self.security_class = raw_data.get('in_system_class', 'unknown')
        else:
            # K-space is the "out" system, Thera is "in"
            self.thera_signature = raw_data.get('in_signature')
            self.exit_system_id = raw_data.get('out_system_id')
            self.exit_system_name = raw_data.get('out_system_name')
            self.exit_signature = raw_data.get('out_signature')
            self.exit_region = raw_data.get('out_region_name', raw_data.get('in_region_name'))
            self.security_class = raw_data.get('out_system_class', raw_data.get('in_system_class', 'unknown'))
        
        self.wh_type = raw_data.get('wh_type')
        self.max_ship_size = raw_data.get('max_ship_size', 'unknown')
        self.remaining_hours = raw_data.get('remaining_hours', 0)
        self.expires_at = raw_data.get('expires_at')
        self.wh_exits_outward = raw_data.get('wh_exits_outward', False)
    
    def meets_size_requirement(self, min_size: str) -> bool:
        """Check if this wormhole meets the minimum size requirement."""
        min_level = WORMHOLE_SIZES.get(min_size.lower(), 0)
        wh_level = WORMHOLE_SIZES.get(self.max_ship_size.lower(), 0)
        return wh_level >= min_level
    
    def get_size_emoji(self) -> str:
        """Get an emoji representing the wormhole size."""
        size_emojis = {
            "small": "🔹",
            "medium": "🔷",
            "large": "🟦",
            "xlarge": "🟪",
            "capital": "🟥"
        }
        return size_emojis.get(self.max_ship_size.lower(), "❓")
    
    def get_security_emoji(self) -> str:
        """Get an emoji representing the security class."""
        sec_emojis = {
            "hs": "🔵",  # High-sec
            "ls": "🟡",  # Low-sec
            "ns": "🔴",  # Null-sec
        }
        return sec_emojis.get(self.security_class.lower(), "⚪")
    
    def get_lifetime_status(self) -> str:
        """Get a human-readable lifetime status."""
        if self.remaining_hours <= 4:
            return f"⚠️ EOL (~{self.remaining_hours}h remaining)"
        elif self.remaining_hours <= 8:
            return f"🕐 ~{self.remaining_hours}h remaining"
        else:
            return f"✅ ~{self.remaining_hours}h remaining"


class TheraBot(commands.Bot):
    """Discord bot for Thera wormhole notifications."""
    
    def __init__(self, config: Config):
        intents = discord.Intents.default()
        intents.message_content = True
        
        super().__init__(
            command_prefix='!thera ',
            intents=intents,
            description="Eve Online Thera Wormhole Connection Bot",
            help_command=None  # Disable default help to use our custom one
        )
        
        self.config = config
        self.http_session: Optional[aiohttp.ClientSession] = None
        self.eve_scout: Optional[EveScoutClient] = None
        self.esi: Optional[ESIClient] = None
        self.scheduler: Optional[AsyncIOScheduler] = None
        
        # Track notified connections to avoid spam
        self.notified_connections: Dict[str, datetime] = {}
        
        # Add commands
        self.add_commands()
    
    def add_commands(self) -> None:
        """Add bot commands."""
        
        @self.command(name='check')
        async def check_connections(ctx):
            """Manually check for Thera connections."""
            await ctx.send("🔍 Checking for Thera connections...")
            await self.check_and_notify()
        
        @self.command(name='status')
        async def bot_status(ctx):
            """Show bot status and configuration."""
            embed = discord.Embed(
                title="🌀 Thera Bot Status",
                color=discord.Color.blue()
            )
            
            # Show departure systems
            departure_list = "\n".join([
                f"• {d['name']} ({d['system_id']})"
                for d in self.config.departure_systems
            ])
            embed.add_field(
                name="Departure Systems",
                value=departure_list or "None configured",
                inline=False
            )
            
            embed.add_field(
                name="Polling Interval",
                value=f"{self.config.polling_interval}s",
                inline=True
            )
            
            destinations = "\n".join([
                f"• {d['name']} (≤{d['max_jumps']}j)"
                for d in self.config.destinations
            ])
            embed.add_field(
                name="Monitored Destinations",
                value=destinations or "None configured",
                inline=False
            )
            
            embed.add_field(
                name="Tracked Connections",
                value=str(len(self.notified_connections)),
                inline=True
            )
            
            await ctx.send(embed=embed)
        
        @self.command(name='list')
        async def list_connections(ctx):
            """List all current Thera connections."""
            if not self.eve_scout:
                await ctx.send("❌ Bot not fully initialized.")
                return
            
            connections = await self.eve_scout.get_thera_connections()
            
            if not connections:
                await ctx.send("No Thera connections currently available.")
                return
            
            embed = discord.Embed(
                title="🌀 Current Thera Connections",
                description=f"Found {len(connections)} connections",
                color=discord.Color.purple()
            )
            
            for conn_data in connections[:10]:  # Limit to 10 to avoid embed limits
                conn = TheraConnection(conn_data)
                
                # Skip if it's a wormhole-to-wormhole connection
                if conn.exit_system_name and conn.exit_system_name.startswith('J'):
                    continue
                
                field_value = (
                    f"{conn.get_security_emoji()} {conn.exit_system_name} ({conn.exit_region})\n"
                    f"Sig: `{conn.thera_signature}` ↔ `{conn.exit_signature}`\n"
                    f"Size: {conn.get_size_emoji()} {conn.max_ship_size.capitalize()}\n"
                    f"{conn.get_lifetime_status()}"
                )
                
                embed.add_field(
                    name=f"WH Type: {conn.wh_type}",
                    value=field_value,
                    inline=True
                )
            
            if len(connections) > 10:
                embed.set_footer(text=f"Showing 10 of {len(connections)} connections")
            
            await ctx.send(embed=embed)
        
        @self.command(name='help')
        async def show_help(ctx):
            """Show help information."""
            embed = discord.Embed(
                title="🌀 Thera Bot Help",
                description="Monitor Thera wormhole connections for your corporation",
                color=discord.Color.green()
            )
            
            commands_text = (
                "`!thera check` - Manually check for good connections\n"
                "`!thera status` - Show bot configuration and status\n"
                "`!thera list` - List all current Thera connections\n"
                "`!thera help` - Show this help message"
            )
            embed.add_field(name="Commands", value=commands_text, inline=False)
            
            embed.add_field(
                name="Automatic Notifications",
                value=f"The bot automatically checks every {self.config.polling_interval} seconds "
                      f"and notifies when a connection meets your criteria.",
                inline=False
            )
            
            await ctx.send(embed=embed)
    
    async def setup_hook(self) -> None:
        """Called when the bot is starting up."""
        # Create HTTP session
        self.http_session = aiohttp.ClientSession()
        self.eve_scout = EveScoutClient(self.http_session)
        self.esi = ESIClient(self.http_session, self.config.route_preference)
        
        # Setup scheduler for periodic checks
        self.scheduler = AsyncIOScheduler()
        self.scheduler.add_job(
            self.check_and_notify,
            'interval',
            seconds=self.config.polling_interval,
            id='thera_check',
            replace_existing=True
        )
        self.scheduler.start()
        
        logging.info("Bot setup complete, scheduler started")
    
    async def on_ready(self) -> None:
        """Called when the bot is ready."""
        logging.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logging.info(f"Connected to {len(self.guilds)} guild(s)")
        
        # Set bot status
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="Thera connections"
            )
        )
        
        # Do an initial check
        await asyncio.sleep(5)  # Wait a bit for everything to be ready
        await self.check_and_notify()
    
    async def close(self) -> None:
        """Clean up when bot is shutting down."""
        if self.scheduler:
            self.scheduler.shutdown()
        if self.http_session:
            await self.http_session.close()
        await super().close()
    
    def _get_route_key(self, departure: Dict, entry_conn: TheraConnection, exit_conn: TheraConnection, destination: Dict) -> str:
        """Generate a unique key for a departure/entry/exit/destination combination."""
        return f"{departure['system_id']}_{entry_conn.id}_{exit_conn.id}_{destination['system_id']}"
    
    def _is_on_cooldown(self, key: str) -> bool:
        """Check if a connection notification is on cooldown."""
        if key not in self.notified_connections:
            return False
        
        last_notified = self.notified_connections[key]
        cooldown = timedelta(seconds=self.config.cooldown_seconds)
        return datetime.now() - last_notified < cooldown
    
    def _clean_old_notifications(self) -> None:
        """Remove old entries from the notification tracker."""
        cutoff = datetime.now() - timedelta(seconds=self.config.cooldown_seconds * 2)
        self.notified_connections = {
            k: v for k, v in self.notified_connections.items()
            if v > cutoff
        }
    
    async def check_and_notify(self) -> None:
        """Check for good Thera connections and send notifications."""
        logging.info("Checking for Thera connections...")
        
        # Clean old notification records
        self._clean_old_notifications()
        
        # Get current Thera connections
        connections = await self.eve_scout.get_thera_connections()
        
        if not connections:
            logging.info("No Thera connections found")
            return
        
        # Process all connections - filter out J-space and prepare for distance calculations
        processed_connections: List[TheraConnection] = []
        for conn_data in connections:
            conn = TheraConnection(conn_data)
            # Skip wormhole-to-wormhole connections (J-space)
            if conn.exit_system_name and conn.exit_system_name.startswith('J'):
                continue
            processed_connections.append(conn)
        
        if not processed_connections:
            logging.info("No k-space Thera connections found")
            return
        
        # Calculate distances from each departure system to each wormhole
        # Structure: {departure_system_id: {conn.id: jumps}}
        departure_to_wh_jumps: Dict[int, Dict[int, int]] = {}
        for departure in self.config.departure_systems:
            dep_id = departure['system_id']
            departure_to_wh_jumps[dep_id] = {}
            for conn in processed_connections:
                jumps = await self.esi.get_route_jumps(dep_id, conn.exit_system_id)
                if jumps is not None:
                    departure_to_wh_jumps[dep_id][conn.id] = jumps
        
        # Calculate distances from each wormhole to each destination
        # Structure: {dest_system_id: {conn.id: jumps}}
        wh_to_dest_jumps: Dict[int, Dict[int, int]] = {}
        for destination in self.config.destinations:
            dest_id = destination['system_id']
            wh_to_dest_jumps[dest_id] = {}
            for conn in processed_connections:
                jumps = await self.esi.get_route_jumps(conn.exit_system_id, dest_id)
                if jumps is not None:
                    wh_to_dest_jumps[dest_id][conn.id] = jumps
        
        # Create a lookup for connections by ID
        conn_by_id: Dict[int, TheraConnection] = {conn.id: conn for conn in processed_connections}
        
        # For each departure/destination pair, find the best entry and exit wormhole combination
        # Route type: (departure, entry_conn, exit_conn, destination, jumps_dep_to_entry, jumps_exit_to_dest)
        good_routes: List[Tuple[Dict, TheraConnection, TheraConnection, Dict, int, int]] = []
        
        for departure in self.config.departure_systems:
            dep_id = departure['system_id']
            dep_jumps = departure_to_wh_jumps.get(dep_id, {})
            
            for destination in self.config.destinations:
                dest_id = destination['system_id']
                dest_jumps = wh_to_dest_jumps.get(dest_id, {})
                
                # Find the best entry wormhole (closest to departure)
                best_entry: Optional[Tuple[TheraConnection, int]] = None
                for conn_id, jumps in dep_jumps.items():
                    if best_entry is None or jumps < best_entry[1]:
                        best_entry = (conn_by_id[conn_id], jumps)
                
                # Find the best exit wormhole (closest to destination)
                best_exit: Optional[Tuple[TheraConnection, int]] = None
                for conn_id, jumps in dest_jumps.items():
                    if best_exit is None or jumps < best_exit[1]:
                        best_exit = (conn_by_id[conn_id], jumps)
                
                # If we found both entry and exit, check if total meets threshold
                if best_entry and best_exit:
                    entry_conn, jumps_dep_to_entry = best_entry
                    exit_conn, jumps_exit_to_dest = best_exit
                    total_jumps = jumps_dep_to_entry + jumps_exit_to_dest
                    
                    if total_jumps <= destination['max_jumps']:
                        # Check cooldown
                        key = self._get_route_key(departure, entry_conn, exit_conn, destination)
                        if not self._is_on_cooldown(key):
                            good_routes.append((departure, entry_conn, exit_conn, destination, jumps_dep_to_entry, jumps_exit_to_dest))
                            self.notified_connections[key] = datetime.now()
        
        # Send notifications for good routes
        if good_routes:
            await self.send_notifications(good_routes)
        else:
            logging.info("No routes meeting criteria found")
    
    async def send_notifications(
        self,
        routes: List[Tuple[Dict, TheraConnection, TheraConnection, Dict, int, int]]
    ) -> None:
        """Send Discord notifications for good routes."""
        channel = self.get_channel(self.config.channel_id)
        
        if not channel:
            logging.error(f"Could not find channel {self.config.channel_id}")
            return
        
        for departure, entry_conn, exit_conn, destination, jumps_dep_to_entry, jumps_exit_to_dest in routes:
            departure_name = departure['name']
            
            # Build mention string
            if self.config.mention_role_id:
                mention = f"<@&{self.config.mention_role_id}>"
            elif self.config.mention_everyone:
                mention = "@everyone"
            else:
                mention = ""
            
            # Check if entry and exit are the same wormhole
            same_wormhole = entry_conn.id == exit_conn.id
            
            # Create embed
            embed = discord.Embed(
                title=f"🌀 Thera Route: {departure_name} → {destination['name']}",
                description=(
                    f"{mention}\n\n"
                    f"A route from **{departure_name}** to **{destination['name']}** is available via Thera!\n\n"
                    f"**{jumps_dep_to_entry}j** from {departure_name} to Thera entry\n"
                    f"**{jumps_exit_to_dest}j** from Thera exit to {destination['name']}\n"
                    f"**Total: {jumps_dep_to_entry + jumps_exit_to_dest} jumps** (+ Thera transit)"
                ),
                color=discord.Color.gold(),
                timestamp=datetime.utcnow()
            )
            
            # Add entry wormhole details (departure side)
            # For entry: K-space sig first (what you scan from departure), then Thera sig
            embed.add_field(
                name=f"🚪 Entry WH ({departure_name} side)",
                value=(
                    f"{entry_conn.get_security_emoji()} **{entry_conn.exit_system_name}**\n"
                    f"Region: {entry_conn.exit_region}\n"
                    f"Sig: `{entry_conn.exit_signature}` → `{entry_conn.thera_signature}`\n"
                    f"Size: {entry_conn.get_size_emoji()} **{entry_conn.max_ship_size.capitalize()}**\n"
                    f"Type: {entry_conn.wh_type} | {entry_conn.get_lifetime_status()}"
                ),
                inline=False
            )
            
            if same_wormhole:
                embed.add_field(
                    name="ℹ️ Same Wormhole",
                    value="Entry and exit use the same wormhole connection",
                    inline=False
                )
            else:
                # Add exit wormhole details (destination side)
                # For exit: Thera sig first (what you scan from Thera), then K-space sig
                embed.add_field(
                    name=f"🚪 Exit WH ({destination['name']} side)",
                    value=(
                        f"{exit_conn.get_security_emoji()} **{exit_conn.exit_system_name}**\n"
                        f"Region: {exit_conn.exit_region}\n"
                        f"Sig: `{exit_conn.thera_signature}` → `{exit_conn.exit_signature}`\n"
                        f"Size: {exit_conn.get_size_emoji()} **{exit_conn.max_ship_size.capitalize()}**\n"
                        f"Type: {exit_conn.wh_type} | {exit_conn.get_lifetime_status()}"
                    ),
                    inline=False
                )
            
            # Add footer with data source
            embed.set_footer(
                text="Data from Eve-Scout • eve-scout.com",
                icon_url="https://www.eve-scout.com/favicon.ico"
            )
            
            try:
                await channel.send(embed=embed)
                logging.info(
                    f"Sent notification for route {departure_name} -> {destination['name']} "
                    f"via {entry_conn.exit_system_name} -> {exit_conn.exit_system_name} "
                    f"({jumps_dep_to_entry + jumps_exit_to_dest} jumps)"
                )
            except discord.DiscordException as e:
                logging.error(f"Failed to send notification: {e}")


def setup_logging(config: Config) -> None:
    """Configure logging for the bot."""
    log_level = getattr(logging, config.log_level.upper(), logging.INFO)
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    
    # File handler
    file_handler = logging.FileHandler(config.log_file, encoding='utf-8')
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    
    # Reduce noise from libraries
    logging.getLogger('discord').setLevel(logging.WARNING)
    logging.getLogger('aiohttp').setLevel(logging.WARNING)
    logging.getLogger('apscheduler').setLevel(logging.WARNING)


def main():
    """Main entry point for the bot."""
    # Load configuration
    try:
        config = Config()
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except ValueError as e:
        print(f"Configuration error: {e}")
        sys.exit(1)
    
    # Setup logging
    setup_logging(config)
    logging.info("Starting Thera Bot...")
    
    # Validate bot token
    if config.bot_token == "YOUR_DISCORD_BOT_TOKEN_HERE":
        logging.error("Please configure your Discord bot token in config.yaml")
        sys.exit(1)
    
    if config.channel_id == int("YOUR_CHANNEL_ID_HERE".replace("YOUR_CHANNEL_ID_HERE", "0")):
        logging.error("Please configure your Discord channel ID in config.yaml")
        sys.exit(1)
    
    # Create and run bot
    bot = TheraBot(config)
    
    try:
        bot.run(config.bot_token)
    except discord.LoginFailure:
        logging.error("Invalid Discord bot token. Please check your configuration.")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Bot crashed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
