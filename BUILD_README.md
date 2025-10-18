# Steamauto Build Guide

This guide explains how to build and run the Steamauto project.

## Prerequisites

- Python 3.8 or higher
- pip (Python package installer)
- Git (for version control)

## Quick Start

### 1. Setup Environment
```bash
# Run the setup script to install dependencies and create directories
python setup.py
```

### 2. Run the Program
```bash
# Run the program directly
python Steamauto.py
```

### 3. Build Executable
```bash
# Build a standalone executable
python build.py
```

## Manual Setup

If you prefer to set up manually:

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Create Directories
```bash
mkdir config logs session plugins
```

### 3. Build Executable
```bash
pyinstaller build.spec
```

## Build Configuration

The build process is configured in `build.spec`:

- **Entry Point**: `Steamauto.py`
- **Output**: Single executable file
- **Platform**: Windows/Linux support
- **Dependencies**: All required modules are included
- **Plugins**: Plugin directory is included in the build

## Project Structure

```
Steamauto/
├── Steamauto.py          # Main application
├── build.spec            # PyInstaller configuration
├── build.py              # Build automation script
├── setup.py              # Setup automation script
├── requirements.txt      # Python dependencies
├── BuffApi/              # Buff API module
├── utils/                # Utility modules
├── plugins/              # Plugin modules
├── steampy/              # Steam API wrapper
├── protobufs/            # Protocol buffer definitions
└── config/               # Configuration files (created on first run)
```

## Dependencies

Key dependencies include:
- `requests` - HTTP requests
- `steampy` - Steam API wrapper
- `pydantic` - Data validation
- `protobuf` - Protocol buffer support
- `pyinstaller` - Executable building
- And many more (see requirements.txt)

## Troubleshooting

### Common Issues

1. **Import Errors**: Make sure all dependencies are installed
   ```bash
   pip install -r requirements.txt
   ```

2. **Build Failures**: Check that all required files exist
   ```bash
   python setup.py
   ```

3. **Missing Modules**: The build.spec includes all necessary hidden imports

### Build Options

- **Debug Build**: Set `debug=True` in build.spec
- **Console Mode**: Set `console=True` in build.spec
- **One File**: Set `onefile=True` in build.spec

## Development

For development, you can run the program directly:
```bash
python Steamauto.py
```

The program will create necessary configuration files on first run.

## Support

If you encounter issues:
1. Check that all dependencies are installed
2. Verify Python version (3.8+)
3. Ensure all required files are present
4. Check the logs directory for error messages
