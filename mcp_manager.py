#!/usr/bin/env python3

import click
import psutil
import yaml
import os
import sys
import json
from rich.console import Console
from rich.table import Table
from rich import print as rprint
import subprocess
from typing import List, Dict, Optional
from pathlib import Path
import signal
import requests
import time
from server_registry import ServerRegistry

console = Console()

class MCPServerManager:
    def __init__(self):
        self.cursor_config_path = os.path.expanduser("~/.cursor/mcp.json")
        self.config = self._load_cursor_config()
        self.registry = ServerRegistry()
        
    def _load_cursor_config(self) -> Dict:
        """Load Cursor's MCP configuration."""
        try:
            with open(self.cursor_config_path, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            rprint(f"[red]Error loading Cursor MCP config: {str(e)}[/red]")
            return {"mcpServers": {}}

    def _find_npm_path(self) -> str:
        """Find the npm executable path."""
        try:
            if os.name == 'nt':  # Windows
                npm_path = subprocess.check_output(['where', 'npm']).decode().strip()
            else:  # Unix-like
                npm_path = subprocess.check_output(['which', 'npm']).decode().strip()
            return npm_path
        except subprocess.CalledProcessError:
            rprint("[red]Error: npm not found. Please install Node.js and npm.[/red]")
            sys.exit(1)

    def detect_cursor_mcp_servers(self) -> List[Dict]:
        """Detect running Cursor MCP servers."""
        mcp_servers = []
        
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'connections']):
            try:
                cmdline = proc.info['cmdline']
                if not cmdline:
                    continue

                # Match against known MCP servers from config
                for server_name, server_config in self.config['mcpServers'].items():
                    if self._is_mcp_server_process(cmdline, server_config):
                        connections = proc.connections()
                        ports = [conn.laddr.port for conn in connections if conn.status == 'LISTEN']
                        
                        server_info = {
                            'name': server_name,
                            'pid': proc.pid,
                            'ports': ports,
                            'command': ' '.join(cmdline),
                            'status': 'Running',
                            'config': server_config
                        }
                        
                        try:
                            server_info['working_dir'] = proc.cwd()
                        except (psutil.AccessDenied, psutil.NoSuchProcess):
                            server_info['working_dir'] = 'N/A'
                        
                        mcp_servers.append(server_info)
                        break

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        return mcp_servers

    def _is_mcp_server_process(self, cmdline: List[str], server_config: Dict) -> bool:
        """Check if a process matches an MCP server configuration."""
        if not cmdline:
            return False
            
        expected_cmd = server_config['command']
        expected_args = server_config['args']
        
        # Convert both to string for easier comparison
        cmd_str = ' '.join(cmdline)
        
        # Check for both the exact command and the npm/npx package name
        for arg in expected_args:
            if '@modelcontextprotocol' in arg or 'mcp-' in arg:
                if arg in cmd_str:
                    return True
        
        return False

    def list_servers(self):
        """List all Cursor MCP servers (both running and configured)."""
        detected_servers = self.detect_cursor_mcp_servers()
        
        table = Table(title="Cursor MCP Servers")
        table.add_column("Name", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Command", style="yellow")
        
        # Add all configured servers
        for name, config in self.config['mcpServers'].items():
            running_server = next((s for s in detected_servers if s['name'] == name), None)
            
            status = "Running" if running_server else "Stopped"
            command = f"{config['command']} {' '.join(config['args'])}"
            
            table.add_row(name, status, command)
        
        console.print(table)

    def start_server(self, server_name: str):
        """Start a specific MCP server."""
        if server_name not in self.config['mcpServers']:
            rprint(f"[red]Error: Server '{server_name}' not found in configuration[/red]")
            return False

        # Check if already running
        running_servers = self.detect_cursor_mcp_servers()
        if any(s['name'] == server_name for s in running_servers):
            rprint(f"[yellow]Server '{server_name}' is already running[/yellow]")
            return True

        server_config = self.config['mcpServers'][server_name]
        cmd = [server_config['command']] + server_config['args']

        try:
            # Start the server in the background
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True  # This makes it run in the background
            )
            rprint(f"[green]Started server '{server_name}' (PID: {process.pid})[/green]")
            return True
        except subprocess.CalledProcessError as e:
            rprint(f"[red]Error starting server '{server_name}': {e}[/red]")
            return False

    def stop_server(self, server_name: str):
        """Stop a specific MCP server."""
        running_servers = self.detect_cursor_mcp_servers()
        server = next((s for s in running_servers if s['name'] == server_name), None)
        
        if not server:
            rprint(f"[yellow]Server '{server_name}' is not running[/yellow]")
            return True  # Return True since there's nothing to stop

        try:
            # Get the process group
            process = psutil.Process(server['pid'])
            
            # Try to get all child processes
            try:
                children = process.children(recursive=True)
                for child in children:
                    try:
                        child.terminate()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

            # Try graceful termination first
            try:
                # Send SIGTERM to the process group
                os.killpg(os.getpgid(server['pid']), signal.SIGTERM)
                time.sleep(1)  # Give it a moment to terminate
            except (ProcessLookupError, ProcessGroupError):
                try:
                    # If process group termination fails, try individual process
                    process.terminate()
                    process.wait(timeout=5)
                except (psutil.TimeoutExpired, psutil.NoSuchProcess):
                    try:
                        # If graceful termination fails, try force kill
                        process.kill()
                        process.wait(timeout=2)
                    except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                        pass  # Process might already be gone
                
            rprint(f"[green]Stopped server '{server_name}'[/green]")
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            rprint(f"[yellow]Server '{server_name}' was already stopped[/yellow]")
            return True
        except Exception as e:
            rprint(f"[red]Error stopping server '{server_name}': {str(e)}[/red]")
            return False

    def get_server_functions(self, server_name: str):
        """Get detailed information about a server's available functions."""
        if server_name not in self.config['mcpServers']:
            rprint(f"[red]Error: Server '{server_name}' not found in configuration[/red]")
            return

        server_config = self.config['mcpServers'][server_name]
        
        # First check if the server is running
        running_servers = self.detect_cursor_mcp_servers()
        running_server = next((s for s in running_servers if s['name'] == server_name), None)
        
        if not running_server:
            rprint(f"[yellow]Note: Server '{server_name}' is not running. Starting it temporarily...[/yellow]")
            self.start_server(server_name)
            # Give it a moment to start
            time.sleep(2)
            running_servers = self.detect_cursor_mcp_servers()
            running_server = next((s for s in running_servers if s['name'] == server_name), None)
            if not running_server:
                rprint(f"[red]Error: Could not start server '{server_name}'[/red]")
                return

        # Try to get server information from package
        try:
            # Find the package name from args
            package_name = None
            for arg in server_config['args']:
                if '@' in arg:
                    package_name = arg
                    break
                elif 'mcp-' in arg or arg.endswith('-mcp'):
                    package_name = arg
                    break

            if not package_name:
                rprint(f"[red]Error: Could not determine package name for '{server_name}'[/red]")
                return

            rprint(f"[bold]Server: {server_name}[/bold]")
            rprint(f"[bold]Package: {package_name}[/bold]")
            rprint()

            # Display available functions
            table = Table(title=f"Available Functions")
            table.add_column("Function Name", style="cyan")
            table.add_column("Description", style="yellow")
            table.add_column("Parameters", style="green")

            # Get function information based on server type
            if 'filesystem' in package_name:
                functions = [
                    ("read_file", "Read the contents of a file", "path: string"),
                    ("write_file", "Write content to a file", "path: string, content: string"),
                    ("list_directory", "List contents of a directory", "path: string"),
                    ("create_directory", "Create a new directory", "path: string"),
                    ("delete_file", "Delete a file", "path: string"),
                    ("move_file", "Move or rename a file", "source: string, destination: string"),
                ]
            elif 'browser-tools' in package_name:
                functions = [
                    ("getConsoleLogs", "Get browser console logs", "None"),
                    ("getConsoleErrors", "Get browser console errors", "None"),
                    ("getNetworkLogs", "Get network request logs", "None"),
                    ("takeScreenshot", "Take a screenshot", "None"),
                    ("runSEOAudit", "Run SEO audit", "None"),
                    ("runDebuggerMode", "Start debugger mode", "None"),
                ]
            elif 'server-llm-txt' in package_name:
                functions = [
                    ("list_llm_txt", "List available LLM.txt files", "None"),
                    ("get_llm_txt", "Get contents of an LLM.txt file", "id: number, page: number"),
                    ("search_llm_txt", "Search within LLM.txt files", "id: number, queries: string[]"),
                ]
            elif 'mcp-shell' in package_name:
                functions = [
                    ("connect", "Connect to an MCP server", "url: string"),
                    ("disconnect", "Disconnect from current server", "None"),
                    ("list", "List available functions", "None"),
                    ("call", "Call a function", "function: string, params: object"),
                    ("help", "Show help for a function", "function: string"),
                ]
            else:
                # Generic MCP server functions
                functions = [
                    ("start", "Start the server", "None"),
                    ("stop", "Stop the server", "None"),
                    ("status", "Get server status", "None"),
                    ("version", "Get server version", "None"),
                ]

            for func_name, description, params in functions:
                table.add_row(func_name, description, params)

            console.print(table)

        except Exception as e:
            rprint(f"[red]Error getting server functions: {str(e)}[/red]")

    def uninstall_server(self, server_name: str):
        """Uninstall a specific MCP server."""
        if server_name not in self.config['mcpServers']:
            rprint(f"[red]Error: Server '{server_name}' not found in configuration[/red]")
            return False

        # First stop the server if it's running
        running_servers = self.detect_cursor_mcp_servers()
        if any(s['name'] == server_name for s in running_servers):
            rprint(f"[yellow]Stopping server '{server_name}' before uninstalling...[/yellow]")
            if not self.stop_server(server_name):
                rprint(f"[red]Failed to stop server '{server_name}'. Aborting uninstall.[/red]")
                return False
            time.sleep(2)  # Give it time to stop

        server_config = self.config['mcpServers'][server_name]
        
        # Try to find the package name from args
        package_name = None
        for arg in server_config['args']:
            if '@' in arg:
                package_name = arg
                break
            elif 'mcp-' in arg or arg.endswith('-mcp'):
                package_name = arg
                break

        if not package_name:
            rprint(f"[red]Error: Could not determine package name for '{server_name}'[/red]")
            return False

        try:
            # Uninstall the package using npm
            rprint(f"[yellow]Uninstalling {package_name}...[/yellow]")
            
            # Use shell=True to prevent process termination issues
            result = subprocess.run(
                f"npm uninstall -g {package_name}",
                shell=True,
                capture_output=True,
                text=True,
                timeout=30  # Add timeout to prevent hanging
            )
            
            if result.returncode != 0:
                rprint(f"[red]Error uninstalling server: {result.stderr}[/red]")
                return False

            # Remove from Cursor's config
            self.config['mcpServers'].pop(server_name)
            
            # Save the updated config
            with open(self.cursor_config_path, 'w') as f:
                json.dump(self.config, f, indent=2)

            rprint(f"[green]Successfully uninstalled '{server_name}'[/green]")
            return True

        except subprocess.TimeoutExpired:
            rprint(f"[red]Timeout while uninstalling {package_name}[/red]")
            return False
        except Exception as e:
            rprint(f"[red]Error: {str(e)}[/red]")
            return False

    def start_all_servers(self):
        """Start all configured MCP servers."""
        success_count = 0
        total_count = len(self.config['mcpServers'])
        
        for server_name in self.config['mcpServers']:
            rprint(f"Starting {server_name}...")
            if self.start_server(server_name):
                success_count += 1
            time.sleep(1)  # Brief pause between starts
        
        rprint(f"[green]Started {success_count} out of {total_count} servers[/green]")

    def stop_all_servers(self):
        """Stop all running MCP servers."""
        running_servers = self.detect_cursor_mcp_servers()
        success_count = 0
        
        for server in running_servers:
            rprint(f"Stopping {server['name']}...")
            if self.stop_server(server['name']):
                success_count += 1
            time.sleep(1)  # Brief pause between stops
        
        rprint(f"[green]Stopped {success_count} out of {len(running_servers)} servers[/green]")

    def list_available_servers(self):
        """List all available servers from the registry."""
        servers = self.registry.get_available_servers()
        
        table = Table(title="Available MCP Servers")
        table.add_column("Name", style="cyan")
        table.add_column("Description", style="yellow")
        table.add_column("Version", style="green")
        table.add_column("Author", style="magenta")
        
        for server in servers:
            table.add_row(
                server['name'],
                server['description'],
                server['version'],
                server['author']
            )
        
        console.print(table)

    def install_from_registry(self, server_name: str):
        """Install a server from the registry."""
        return self.registry.install_server(server_name)

    def update_registry(self):
        """Update the local registry cache."""
        self.registry.update_registry()
        rprint("[green]Registry updated successfully[/green]")

@click.group()
def cli():
    """Cursor MCP Server Manager - Detect and manage Cursor MCP servers."""
    pass

@cli.command()
def list():
    """List all Cursor MCP servers and their status."""
    manager = MCPServerManager()
    manager.list_servers()

@cli.command()
@click.argument('name')
def start(name):
    """Start a specific MCP server."""
    manager = MCPServerManager()
    manager.start_server(name)

@cli.command()
@click.argument('name')
def stop(name):
    """Stop a specific MCP server."""
    manager = MCPServerManager()
    manager.stop_server(name)

@cli.command()
@click.argument('name')
def restart(name):
    """Restart a specific MCP server."""
    manager = MCPServerManager()
    manager.stop_server(name)
    click.echo("Waiting for server to stop...")
    time.sleep(2)  # Give it a moment to fully stop
    manager.start_server(name)

@cli.command()
@click.argument('name')
def functions(name):
    """Display available functions for a specific MCP server."""
    manager = MCPServerManager()
    manager.get_server_functions(name)

@cli.command()
@click.argument('name')
def uninstall(name):
    """Uninstall a specific MCP server."""
    if click.confirm(f"Are you sure you want to uninstall '{name}'?"):
        manager = MCPServerManager()
        manager.uninstall_server(name)

@cli.command()
def start_all():
    """Start all configured MCP servers."""
    manager = MCPServerManager()
    manager.start_all_servers()

@cli.command()
def stop_all():
    """Stop all running MCP servers."""
    manager = MCPServerManager()
    manager.stop_all_servers()

@cli.command()
def available():
    """List all available servers from the registry."""
    manager = MCPServerManager()
    manager.list_available_servers()

@cli.command()
@click.argument('name')
def install(name):
    """Install a server from the registry."""
    manager = MCPServerManager()
    manager.install_from_registry(name)

@cli.command()
def update():
    """Update the local registry cache."""
    manager = MCPServerManager()
    manager.update_registry()

if __name__ == '__main__':
    cli()