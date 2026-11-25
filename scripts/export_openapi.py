from pathlib import Path
import json, sys
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "services/backend"))
from app.main import app
(root / "contracts/openapi.json").write_text(json.dumps(app.openapi(), indent=2)+"\n")
