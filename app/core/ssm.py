import os

PLACEHOLDER = "CHANGE_ME"


def load_into_env(client=None) -> None:
    """Copies every parameter under $SSM_PREFIX into os.environ. Existing env vars win. No-op when unset."""
    prefix = os.environ.get("SSM_PREFIX")
    if not prefix:
        return
    if client is None:
        import boto3
        client = boto3.client("ssm")
    prefix = prefix.rstrip("/") + "/"
    pages = client.get_paginator("get_parameters_by_path").paginate(Path=prefix, WithDecryption=True)
    for page in pages:
        for p in page["Parameters"]:
            name = p["Name"].removeprefix(prefix)
            if name in os.environ:
                continue
            # Terraform creates secrets with this value; fail here, not on a confusing later error.
            if p["Value"] == PLACEHOLDER:
                raise RuntimeError(f"SSM parameter {p['Name']} is still {PLACEHOLDER}")
            os.environ[name] = p["Value"]
