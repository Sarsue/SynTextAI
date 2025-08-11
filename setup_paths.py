"""
Path setup and configuration for SynTextAI.

This module ensures that all imports work correctly by adding the project root
to the Python path and configuring the environment.
"""

import sys
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Load environment variables
from dotenv import load_dotenv

env_path = project_root / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
    print(f"Loaded environment variables from {env_path}")
else:
    print(f"Warning: .env file not found at {env_path}")
