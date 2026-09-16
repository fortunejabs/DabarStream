"""Run validation with a durable startup marker and bounded execution time."""
from pathlib import Path
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parent
RESULT = ROOT / "validation_result.txt"


def main():
    with RESULT.open("w", encoding="utf-8", buffering=1) as log:
        log.write(f"STARTED\nPython: {sys.executable}\nProject: {ROOT}\n")
        try:
            result = subprocess.run(
                [sys.executable, "-X", "utf8", "-m", "pytest",
                 str(ROOT / "test_importer.py"),
                 str(ROOT / "test_language.py"), "-q"],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=60,
                check=False,
            )
            log.write(f"\nPYTEST_EXIT={result.returncode}\nCOMPLETED\n")
            return result.returncode
        except subprocess.TimeoutExpired:
            log.write("\nTIMEOUT: pytest exceeded 60 seconds.\n")
            return 124
        except Exception:
            traceback.print_exc(file=log)
            return 1


if __name__ == "__main__":
    sys.exit(main())
