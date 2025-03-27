# MCP Server Manager

A simple command-line tool to manage MCP servers on MacOS.

## Installation

1. Clone this repository
2. Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

### List Servers
```bash
python mcp_manager.py list
```

### Add a New Server
```bash
python mcp_manager.py add SERVER_NAME --port PORT_NUMBER
```

## Features

- List configured MCP servers
- Add new server configurations
- Monitor server status
- View server details including port and PID

## Requirements

- Python 3.7+
- MacOS
- Required Python packages (see requirements.txt)