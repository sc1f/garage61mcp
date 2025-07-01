#!/usr/bin/env python3
"""Installation script for Garage61 MCP server with Claude Desktop configuration."""

import os
import sys
import json
import subprocess
import platform
from pathlib import Path

def find_python_path():
    """Find the full path to the Python executable."""
    # Try common locations
    candidates = [
        sys.executable,
        "/usr/bin/python3",
        "/usr/local/bin/python3", 
        "/opt/homebrew/bin/python3",  # M1 Mac Homebrew
        "/usr/bin/python",
    ]
    
    for candidate in candidates:
        if os.path.isfile(candidate):
            try:
                # Test if this python works
                result = subprocess.run([candidate, "--version"], 
                                      capture_output=True, text=True)
                if result.returncode == 0 and "Python 3" in result.stdout:
                    return candidate
            except:
                continue
    
    return sys.executable

def get_claude_config_path():
    """Get the Claude Desktop configuration file path based on OS."""
    system = platform.system()
    
    if system == "Darwin":  # macOS
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif system == "Windows":
        return Path.home() / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    elif system == "Linux":
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
    else:
        raise ValueError(f"Unsupported operating system: {system}")

def install_package():
    """Install the garage61-mcp package."""
    print("Installing garage61-mcp package...")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-e", "."], check=True)
        print("✅ Package installed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to install package: {e}")
        return False

def setup_claude_config():
    """Set up Claude Desktop configuration."""
    config_path = get_claude_config_path()
    python_path = find_python_path()
    
    print(f"Setting up Claude Desktop config at: {config_path}")
    print(f"Using Python executable: {python_path}")
    
    # Create config directory if it doesn't exist
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Read existing config or create new one
    if config_path.exists():
        with open(config_path, 'r') as f:
            config = json.load(f)
    else:
        config = {}
    
    # Ensure mcpServers section exists
    if "mcpServers" not in config:
        config["mcpServers"] = {}
    
    # Add garage61 server configuration
    config["mcpServers"]["garage61"] = {
        "command": python_path,
        "args": ["__main__.py"],
        "cwd": str(Path.cwd() / "src"),
        "env": {
            "GARAGE61_TOKEN": "your-garage61-token-here"
        }
    }
    
    # Write config back
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
    
    print("✅ Claude Desktop configuration updated")
    print(f"\n📝 Next steps:")
    print(f"1. Edit {config_path}")
    print(f"2. Replace 'your-garage61-token-here' with your actual Garage61 API token")
    print(f"3. Restart Claude Desktop")
    
    return config_path

def main():
    """Main installation process."""
    print("🏁 Garage61 MCP Server Installation")
    print("=" * 40)
    
    # Check if we're in the right directory
    if not Path("pyproject.toml").exists():
        print("❌ Please run this script from the garage61-mcp project directory")
        sys.exit(1)
    
    # Install package
    if not install_package():
        sys.exit(1)
    
    # Test installation
    print("\nTesting installation...")
    try:
        result = subprocess.run([sys.executable, "-c", "import garage61_mcp; print('Import successful')"], 
                              capture_output=True, text=True, check=True)
        print("✅ Package import test passed")
    except subprocess.CalledProcessError:
        print("❌ Package import test failed")
        sys.exit(1)
    
    # Setup Claude config
    config_path = setup_claude_config()
    
    print(f"\n🎉 Installation complete!")
    print(f"\n📋 Summary:")
    print(f"   • Package installed in development mode")
    print(f"   • Claude config updated: {config_path}")
    print(f"   • Command available: garage61-mcp")
    
    print(f"\n⚙️  Manual steps needed:")
    print(f"   1. Get your API token from https://garage61.net")
    print(f"   2. Edit the config file and replace the token")
    print(f"   3. Restart Claude Desktop")

if __name__ == "__main__":
    main()