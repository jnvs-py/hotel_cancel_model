"""Cliente de demostracion. Salida en ASCII puro."""

import argparse
import json

import httpx

from src.config import REPO_ROOT

EJEMPLO = REPO_ROOT / "tests" / "fixtures" / "request_ejemplo.json"


def main():
    parser = argparse.ArgumentParser(description="Consume la API de cancelacion de reservas")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--payload", default=str(EJEMPLO))
    args = parser.parse_args()

    payload = json.loads(open(args.payload, encoding="utf-8").read())

    print(f"-> GET  {args.url}/health")
    print("  ", httpx.get(f"{args.url}/health", timeout=10).json())

    print(f"-> GET  {args.url}/model-info")
    info = httpx.get(f"{args.url}/model-info", timeout=10).json()
    print(f"   version {info.get('model_version')}, umbral {info.get('threshold')}")

    print(f"-> POST {args.url}/predict")
    r = httpx.post(f"{args.url}/predict", json=payload, timeout=10)
    print("  ", r.status_code, r.json())


if __name__ == "__main__":
    main()
