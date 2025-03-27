import json
import requests
from pathlib import Path
from typing import Dict, List, Optional
from rich import print as rprint
import time
from datetime import datetime
import subprocess

class ServerRegistry:
    def __init__(self, registry_url: str = "https://raw.githubusercontent.com/OJamals/mcp-registry/main/registry.json"):
        self.registry_url = registry_url
        self.local_registry_path = Path.home() / ".cursor" / "mcp_registry.json"
        self.cache_duration = 3600  # 1 hour in seconds

    def _load_local_registry(self) -> Optional[Dict]:
        """Load the local registry file if it exists and is not expired."""
        try:
            if not self.local_registry_path.exists():
                return None

            with open(self.local_registry_path, 'r') as f:
                data = json.load(f)
                
            # Check if cache is expired
            last_updated = datetime.fromisoformat(data.get('last_updated', '2000-01-01'))
            if (datetime.now() - last_updated).total_seconds() > self.cache_duration:
                return None
                
            return data
        except Exception as e:
            rprint(f"[yellow]Warning: Could not load local registry: {str(e)}[/yellow]")
            return None

    def _save_local_registry(self, data: Dict):
        """Save the registry data to local file."""
        try:
            self.local_registry_path.parent.mkdir(parents=True, exist_ok=True)
            data['last_updated'] = datetime.now().isoformat()
            with open(self.local_registry_path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            rprint(f"[red]Error saving local registry: {str(e)}[/red]")

    def update_registry(self) -> Dict:
        """Fetch the latest registry from the remote URL."""
        try:
            # Add timestamp to bypass caching
            timestamp = int(time.time())
            url = f"{self.registry_url}?t={timestamp}"
            rprint(f"[yellow]Fetching registry from: {url}[/yellow]")
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
            rprint(f"[green]Successfully fetched registry with {len(data.get('servers', {}))} servers[/green]")
            self._save_local_registry(data)
            return data
        except Exception as e:
            rprint(f"[red]Error updating registry: {str(e)}[/red]")
            return self._load_local_registry() or {'servers': {}}

    def get_available_servers(self) -> List[Dict]:
        """Get list of available servers from the registry."""
        data = self._load_local_registry()
        if not data:
            data = self.update_registry()
        return list(data.get('servers', {}).values())

    def get_server_info(self, server_name: str) -> Optional[Dict]:
        """Get information about a specific server."""
        data = self._load_local_registry()
        if not data:
            data = self.update_registry()
        return data.get('servers', {}).get(server_name)

    def install_server(self, server_name: str) -> bool:
        """Install a server from the registry."""
        server_info = self.get_server_info(server_name)
        if not server_info:
            rprint(f"[red]Error: Server '{server_name}' not found in registry[/red]")
            return False

        try:
            # Install the package using npm
            package_name = server_info['package_name']
            rprint(f"[yellow]Installing {package_name}...[/yellow]")
            
            result = subprocess.run(
                f"npm install -g {package_name}",
                shell=True,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                rprint(f"[red]Error installing server: {result.stderr}[/red]")
                return False

            # Add to Cursor's config
            cursor_config_path = Path.home() / ".cursor" / "mcp.json"
            try:
                with open(cursor_config_path, 'r') as f:
                    config = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                config = {"mcpServers": {}}

            config['mcpServers'][server_name] = {
                'command': server_info['command'],
                'args': server_info['args'],
                'description': server_info.get('description', '')
            }

            with open(cursor_config_path, 'w') as f:
                json.dump(config, f, indent=2)

            rprint(f"[green]Successfully installed '{server_name}'[/green]")
            return True

        except Exception as e:
            rprint(f"[red]Error installing server: {str(e)}[/red]")
            return False 