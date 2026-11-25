# CHANGE: YAML generation (23/11/2026). Shared YAML storage on S3, so every laptop and CI runner uses the same config/ YAML.
# Turned on by an 's3:' block in creds/<environment>.yaml (bucket, prefix, profile, aws_region). With no block nothing changes.
# main.py calls pull() before the YAML is read and push() after YAML generation. The validators still read local files.
# For each file under config/: same content (md5) -> nothing; different -> the newer copy wins (pull downloads, push uploads).
# Nothing is ever deleted, on either side.
import hashlib
import os

import yaml


def _settings(base_dir, env):
    with open(os.path.join(base_dir, "creds", f"{env}.yaml")) as f:
        s3 = (yaml.safe_load(f) or {}).get("s3")
    if not s3:
        return None
    if not s3.get("bucket"):
        raise ValueError(f"creds/{env}.yaml: the s3 block has no bucket")
    return s3


def _client(s3):
    import boto3
    return boto3.Session(profile_name=s3.get("profile") or None,
                         region_name=s3.get("aws_region") or None).client("s3")


def _prefix(s3):
    return (s3.get("prefix") or "config").strip("/")


def _md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def _local(config_dir):
    files = {}
    for root, _, names in os.walk(config_dir):
        for name in names:
            path = os.path.join(root, name)
            files[os.path.relpath(path, config_dir).replace(os.sep, "/")] = (_md5(path), os.path.getmtime(path))
    return files


def _remote(client, bucket, prefix):
    files = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix + "/"):
        for obj in page.get("Contents", []):
            rel = obj["Key"][len(prefix) + 1:]
            if rel and not rel.endswith("/"):
                # ETag is the md5 for a single-part upload (YAML files are far below the 8 MB multipart size).
                files[rel] = (obj["ETag"].strip('"'), obj["LastModified"].timestamp())
    return files


def plan(local, remote):
    """(to download, to upload): files missing on one side, or different and newer on the other side."""
    # ponytail: newest-wins on timestamps (laptop clock vs S3 clock). Two people editing one file: the later one wins and
    # the earlier is kept as an older version in the bucket (versioning on). Add locking or review if that becomes common.
    down = [rel for rel, (md5, at) in remote.items()
            if rel not in local or (local[rel][0] != md5 and at > local[rel][1])]
    up = [rel for rel, (md5, at) in local.items()
          if rel not in remote or (remote[rel][0] != md5 and at > remote[rel][1])]
    return sorted(down), sorted(up)


def pull(base_dir, env, log):
    """Download newer YAML from S3 into config/. Raises on any S3 error, so the run does not use an old copy."""
    s3 = _settings(base_dir, env)
    if s3 is None:
        log.info("S3 YAML storage: off (no s3 block in creds/%s.yaml), using local config/", env)
        return
    client, bucket, prefix = _client(s3), s3["bucket"], _prefix(s3)
    config_dir = os.path.join(base_dir, "config")
    remote = _remote(client, bucket, prefix)
    down, _ = plan(_local(config_dir), remote)
    for rel in down:
        path = os.path.join(config_dir, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        client.download_file(bucket, f"{prefix}/{rel}", path)
        # The file gets the S3 time, so it is not seen as a local edit and uploaded back.
        os.utime(path, (remote[rel][1], remote[rel][1]))
    log.info("S3 YAML storage: %d file(s) downloaded from s3://%s/%s/", len(down), bucket, prefix)


def push(base_dir, env, log):
    """Upload new or locally changed YAML in config/ to S3. Raises on any S3 error."""
    s3 = _settings(base_dir, env)
    if s3 is None:
        return
    client, bucket, prefix = _client(s3), s3["bucket"], _prefix(s3)
    config_dir = os.path.join(base_dir, "config")
    _, up = plan(_local(config_dir), _remote(client, bucket, prefix))
    for rel in up:
        client.upload_file(os.path.join(config_dir, *rel.split("/")), bucket, f"{prefix}/{rel}")
    log.info("S3 YAML storage: %d file(s) uploaded to s3://%s/%s/%s", len(up), bucket, prefix,
             ("  " + ", ".join(up)) if up else "")


if __name__ == "__main__":
    same, old, new = ("a", 100.0), ("b", 100.0), ("c", 200.0)
    assert plan({"x": same}, {"x": same}) == ([], [])                  # same content: nothing
    assert plan({}, {"x": same}) == (["x"], [])                         # only on S3: download
    assert plan({"x": same}, {}) == ([], ["x"])                         # only local: upload
    assert plan({"x": old}, {"x": new}) == (["x"], [])                  # S3 newer: download
    assert plan({"x": new}, {"x": old}) == ([], ["x"])                  # local newer: upload
    print("s3_yaml_store plan: ok")
