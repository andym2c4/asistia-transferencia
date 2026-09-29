"""Enviar un script sin secretos por SSM y consultar su estado.

El perfil personal es del operador; la instancia solo usa su rol IAM.
"""

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def aws(*args):
    command = [
        "aws",
        "--profile",
        "personal",
        "--region",
        "us-east-1",
        *args,
        "--output",
        "json",
    ]
    return json.loads(subprocess.check_output(command))


def state():
    data = aws(
        "cloudformation", "describe-stacks", "--stack-name", "asistia-production"
    )["Stacks"][0]
    result = {v["OutputKey"]: v["OutputValue"] for v in data.get("Outputs", [])}
    result["Status"] = data["StackStatus"]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["state", "send", "result"])
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    config = state()
    if args.action == "state":
        print(json.dumps(config, indent=2))
    elif args.action == "send":
        script = Path(args.value).read_text()
        result = aws(
            "ssm",
            "send-command",
            "--instance-ids",
            config["InstanceId"],
            "--document-name",
            "AWS-RunShellScript",
            "--parameters",
            json.dumps({"commands": [script], "executionTimeout": ["3600"]}),
            "--comment",
            "ASISTIA deployment/verification",
        )
        print(result["Command"]["CommandId"])
    else:
        result = aws(
            "ssm",
            "get-command-invocation",
            "--command-id",
            args.value,
            "--instance-id",
            config["InstanceId"],
        )
        print(
            json.dumps(
                {
                    k: result.get(k)
                    for k in [
                        "Status",
                        "ResponseCode",
                        "StandardOutputContent",
                        "StandardErrorContent",
                    ]
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
