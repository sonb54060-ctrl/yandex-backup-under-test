"""Versioned reads and conditional control-state updates over the documented S3 API."""

import hashlib
import json

from botocore.exceptions import ClientError


class Conflict(Exception):
    """Another invocation changed the control state."""


class ObjectProblem(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def error_code(exc):
    return str(exc.response.get("Error", {}).get("Code", "UNKNOWN"))


class Storage:
    def __init__(self, client, control_bucket):
        self.client = client
        self.control_bucket = control_bucket

    def read_json(self, key):
        try:
            response = self.client.get_object(Bucket=self.control_bucket, Key=key)
        except ClientError as exc:
            if error_code(exc) in {"NoSuchKey", "404"}:
                return None, None
            raise
        with response["Body"] as stream:
            return json.loads(stream.read()), response["ETag"]

    def write_json(self, key, value, etag=None, conditional=False):
        args = {}
        if conditional:
            args = {"IfMatch": etag} if etag else {"IfNoneMatch": "*"}
        try:
            response = self.client.put_object(
                Bucket=self.control_bucket,
                Key=key,
                Body=json.dumps(value, ensure_ascii=False, sort_keys=True).encode(),
                ContentType="application/json; charset=utf-8",
                **args,
            )
        except ClientError as exc:
            if error_code(exc) in {
                "PreconditionFailed",
                "ConditionalRequestConflict",
                "412",
                "409",
            }:
                raise Conflict from exc
            raise
        return response["ETag"]

    def write_html(self, key, html):
        self.client.put_object(
            Bucket=self.control_bucket,
            Key=key,
            Body=html.encode(),
            ContentType="text/html; charset=utf-8",
            ContentDisposition="attachment",
        )

    def attachment_hash(self, bucket, key, version, max_bytes):
        if not version or version == "null":
            raise ObjectProblem("UNVERSIONED_REFERENCE")
        try:
            response = self.client.get_object(Bucket=bucket, Key=key, VersionId=version)
            with response["Body"] as stream:
                if response.get("ContentLength", 0) > max_bytes:
                    raise ObjectProblem("OBJECT_TOO_LARGE")
                if response.get("VersionId") != version:
                    raise ObjectProblem("WRONG_VERSION_RETURNED")
                total = 0
                digest = hashlib.sha256()
                while chunk := stream.read(64 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ObjectProblem("OBJECT_TOO_LARGE")
                    digest.update(chunk)
                return digest.hexdigest()
        except ClientError as exc:
            code = error_code(exc)
            if code in {"NoSuchKey", "NoSuchVersion", "NoSuchBucket", "404"}:
                raise ObjectProblem("MISSING_VERSION") from exc
            if code in {"AccessDenied", "403"}:
                raise ObjectProblem("ACCESS_DENIED") from exc
            raise ObjectProblem("STORAGE_ERROR") from exc
