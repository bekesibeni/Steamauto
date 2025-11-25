#!/usr/bin/env python3
"""
Build script for Steamauto
This script automates the build process using PyInstaller
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

def run_command(command, description):
    """Run a command and handle errors"""
    print(f"\n{'='*50}")
    print(f"Running: {description}")
    print(f"Command: {command}")
    print(f"{'='*50}")
    
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print("STDOUT:", result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {e}")
        print("STDOUT:", e.stdout)
        print("STDERR:", e.stderr)
        return False

def clean_build():
    """Clean previous build artifacts"""
    print("Cleaning previous build artifacts...")
    
    # Remove build directories
    dirs_to_clean = ['build', 'dist', '__pycache__']
    for dir_name in dirs_to_clean:
        if os.path.exists(dir_name):
            shutil.rmtree(dir_name)
            print(f"Removed {dir_name}/")
    
    # Remove .spec files (except build.spec)
    for spec_file in Path('.').glob('*.spec'):
        if spec_file.name != 'build.spec':
            spec_file.unlink()
            print(f"Removed {spec_file}")

def install_dependencies():
    """Install required dependencies"""
    print("Installing dependencies...")
    
    # Install from requirements.txt
    if os.path.exists('requirements.txt'):
        # Use sys.executable to use the same Python that's running this script
        python_cmd = sys.executable
        return run_command(f'{python_cmd} -m pip install -r requirements.txt', 'Installing requirements')
    else:
        print("requirements.txt not found!")
        return False

def build_executable():
    """Build the executable using PyInstaller"""
    print("Building executable...")
    
    # Set environment variable for build
    os.environ['MATRIX_OS'] = 'windows' if os.name == 'nt' else 'linux'
    
    # Run PyInstaller with the spec file
    # Use sys.executable to use the same Python that's running this script
    python_cmd = sys.executable
    return run_command(f'{python_cmd} -m PyInstaller build.spec', 'Building executable with PyInstaller')

def main():
    """Main build process"""
    print("Steamauto Build Script")
    print("=" * 50)
    
    # Check if we're in the right directory
    if not os.path.exists('Steamauto.py'):
        print("Error: Steamauto.py not found. Please run this script from the project root.")
        sys.exit(1)
    
    if not os.path.exists('build.spec'):
        print("Error: build.spec not found. Please ensure the build configuration exists.")
        sys.exit(1)
    
    # Clean previous builds
    clean_build()
    
    # Install dependencies
    if not install_dependencies():
        print("Failed to install dependencies!")
        sys.exit(1)
    
    # Build executable
    if not build_executable():
        print("Failed to build executable!")
        sys.exit(1)
    
    print("\n" + "="*50)
    print("Build completed successfully!")
    print("Executable should be in the 'dist' directory")
    print("="*50)

if __name__ == "__main__":
    main()
