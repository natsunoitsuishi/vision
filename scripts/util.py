from pathlib import Path
import sys
def get_project_root() -> Path:
    return Path(__file__).parent.parent

def get_project_config_path() -> Path:
    return get_project_root() / "config" / "default.yaml"

if __name__ == '__main__':
    print(get_project_config_path())