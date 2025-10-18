#!/usr/bin/env python3
"""
Setup script for Steamauto
This script helps set up the development environment
"""

import os
import sys
import subprocess
from pathlib import Path

def check_python_version():
    """Check if Python version is compatible"""
    if sys.version_info < (3, 8):
        print("Error: Python 3.8 or higher is required")
        print(f"Current version: {sys.version}")
        return False
    print(f"Python version: {sys.version} ✓")
    return True

def install_dependencies():
    """Install required dependencies"""
    print("Installing dependencies from requirements.txt...")
    
    try:
        subprocess.run(['py', '-m', 'pip', 'install', '-r', 'requirements.txt'], 
                      check=True)
        print("Dependencies installed successfully ✓")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to install dependencies: {e}")
        return False

def create_directories():
    """Create necessary directories"""
    print("Creating necessary directories...")
    
    directories = [
        'config',
        'logs', 
        'session',
        'plugins'
    ]
    
    for directory in directories:
        Path(directory).mkdir(exist_ok=True)
        print(f"Created/verified directory: {directory}/ ✓")
    
    return True

def create_config_files():
    """Create default configuration files if they don't exist"""
    print("Setting up configuration files...")
    
    # Create config directory if it doesn't exist
    config_dir = Path('config')
    config_dir.mkdir(exist_ok=True)
    
    # Check if config files exist
    config_files = [
        'config/config.json5',
        'config/steam_account_info.json5'
    ]
    
    for config_file in config_files:
        if not Path(config_file).exists():
            print(f"Note: {config_file} will be created on first run")
        else:
            print(f"Found existing config: {config_file} ✓")
    
    return True

def verify_setup():
    """Verify that the setup is correct"""
    print("Verifying setup...")
    
    # Check if main files exist
    required_files = [
        'Steamauto.py',
        'build.spec',
        'requirements.txt'
    ]
    
    for file in required_files:
        if not Path(file).exists():
            print(f"Error: Required file {file} not found!")
            return False
        print(f"Found: {file} ✓")
    
    return True

def main():
    """Main setup process"""
    print("Steamauto Setup Script")
    print("=" * 50)
    
    # Check Python version
    if not check_python_version():
        sys.exit(1)
    
    # Create directories
    if not create_directories():
        print("Failed to create directories!")
        sys.exit(1)
    
    # Create config files
    if not create_config_files():
        print("Failed to setup configuration files!")
        sys.exit(1)
    
    # Install dependencies
    if not install_dependencies():
        print("Failed to install dependencies!")
        sys.exit(1)
    
    # Verify setup
    if not verify_setup():
        print("Setup verification failed!")
        sys.exit(1)
    
    print("\n" + "="*50)
    print("Setup completed successfully!")
    print("You can now:")
    print("1. Run the program: python Steamauto.py")
    print("2. Build executable: python build.py")
    print("="*50)

if __name__ == "__main__":
    main()
